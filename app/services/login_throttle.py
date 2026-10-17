"""Ochrana prihlásenia pred hádaním hesiel (brute force).

Počíta neúspešné pokusy v pamäti appky (jeden proces, aký spúšťa
``manage.py serve``) v dvoch rozsahoch:

  - dvojica (IP adresa, používateľské meno): po ``max_attempts``
    neúspešných pokusoch je táto dvojica zablokovaná na
    ``lockout_seconds``. Útočník z cudzej IP tak nezamkne cudzí účet -
    skutočný používateľ sa prihlási z inej adresy bez problémov,
  - samotná IP adresa: po ``max_attempts_per_ip`` neúspešných pokusoch
    (s ľubovoľnými menami) je celá IP zablokovaná - zastaví skúšanie
    veľa mien z jednej adresy.

Zablokovanie platí aj pre správne heslo (inak by sa dalo hádať ďalej).
Úspešné prihlásenie vynuluje počítadlo danej dvojice. Stav sa po
reštarte appky stratí (blokovanie sa tým len uvoľní).

Za reverzným proxy musí byť nastavené ``BEHIND_PROXY=1``, inak by appka
videla ako adresu klienta adresu proxy a zablokovala by všetkých naraz.
"""

import math
import os
import threading
import time
from collections import deque

# Pri takomto počte sledovaných kľúčov sa vyčistia už neplatné záznamy
# (obrana proti zaplneniu pamäte náhodnými menami).
_CLEANUP_THRESHOLD = 10_000


def _int_from_env(name, default):
    try:
        value = int(os.environ.get(name, "").strip())
    except ValueError:
        return default

    return value if value > 0 else default


class LoginThrottle:
    def __init__(
        self,
        max_attempts=5,
        max_attempts_per_ip=20,
        lockout_seconds=900,
        clock=time.monotonic,
    ):
        self.max_attempts = max_attempts
        self.max_attempts_per_ip = max_attempts_per_ip
        self.lockout_seconds = lockout_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = {}
        self._blocked_until = {}

    @classmethod
    def from_env(cls):
        """Nastavenie z prostredia: LOGIN_MAX_ATTEMPTS (5),
        LOGIN_IP_MAX_ATTEMPTS (20), LOGIN_LOCKOUT_MINUTES (15)."""

        return cls(
            max_attempts=_int_from_env("LOGIN_MAX_ATTEMPTS", 5),
            max_attempts_per_ip=_int_from_env("LOGIN_IP_MAX_ATTEMPTS", 20),
            lockout_seconds=_int_from_env("LOGIN_LOCKOUT_MINUTES", 15) * 60,
        )

    @staticmethod
    def _keys(ip, username):
        return ("pair", ip, username), ("ip", ip)

    def retry_after(self, ip, username):
        """Za koľko sekúnd sa smie pokus zopakovať (0 = smie hneď)."""

        now = self._clock()

        with self._lock:
            remaining = max(
                (
                    self._blocked_until.get(key, 0) - now
                    for key in self._keys(ip, username)
                ),
                default=0,
            )

        return max(0, math.ceil(remaining))

    def record_failure(self, ip, username):
        now = self._clock()
        pair_key, ip_key = self._keys(ip, username)

        with self._lock:
            self._cleanup(now)

            for key, limit in (
                (pair_key, self.max_attempts),
                (ip_key, self.max_attempts_per_ip),
            ):
                attempts = self._failures.setdefault(key, deque())

                while attempts and attempts[0] <= now - self.lockout_seconds:
                    attempts.popleft()

                attempts.append(now)

                if len(attempts) >= limit:
                    self._blocked_until[key] = now + self.lockout_seconds
                    attempts.clear()

    def record_success(self, ip, username):
        """Úspešné prihlásenie vynuluje počítadlo dvojice (IP, meno).
        Počítadlo samotnej IP sa nemení - za jednou adresou (NAT) môžu
        sedieť aj iní."""

        pair_key, _ = self._keys(ip, username)

        with self._lock:
            self._failures.pop(pair_key, None)
            self._blocked_until.pop(pair_key, None)

    def _cleanup(self, now):
        if len(self._failures) + len(self._blocked_until) < _CLEANUP_THRESHOLD:
            return

        for key in [k for k, until in self._blocked_until.items() if until <= now]:
            del self._blocked_until[key]

        for key in list(self._failures):
            attempts = self._failures[key]

            while attempts and attempts[0] <= now - self.lockout_seconds:
                attempts.popleft()

            if not attempts:
                del self._failures[key]

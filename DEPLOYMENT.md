# Nasadenie pre viac pobočiek

Cieľ: **jedna centrálna inštancia** appky, ku ktorej sa pobočky
pripájajú cez prehliadač na `https://plan.firma.sk`.

```
prehliadač (pobočka) --HTTPS--> Caddy (443) --> waitress (127.0.0.1:8000) --> PostgreSQL
```

- **PostgreSQL** - databáza (SQLite pri viacerých súbežných používateľoch
  blokuje zápisy).
- **waitress** - produkčný server appky (`python manage.py serve`), bez
  debuggera. Počúva len na `127.0.0.1`.
- **Caddy** - reverzný proxy, ktorý zabezpečí HTTPS (certifikát si
  vybaví a obnoví sám).

Potrebuješ: server (Linux alebo Windows), doménu s A záznamom na jeho IP
a otvorené porty 80 a 443.

> Poznámka: postup s PostgreSQL, importom dát, `manage.py serve`,
> zálohami a hlavičkami je overený testami. Konfigurácia Caddy a služieb
> (systemd, NSSM) je napísaná podľa ich dokumentácie, ale nebola
> spúšťaná v tomto vývojovom prostredí - po nasadení si ju over.

---

## 1. PostgreSQL

### Linux (Ubuntu/Debian)

```bash
sudo apt install -y postgresql postgresql-client
sudo -u postgres psql -c "CREATE USER planner WITH PASSWORD 'ZMEN-TO-HESLO';"
sudo -u postgres psql -c "CREATE DATABASE shift_planner OWNER planner;"
```

### Windows

Stiahni inštalátor z postgresql.org, nainštaluj (spolu s "Command Line
Tools"). Potom v PowerShelli (cesta podľa verzie):

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "CREATE USER planner WITH PASSWORD 'ZMEN-TO-HESLO';"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "CREATE DATABASE shift_planner OWNER planner;"
```

Priečinok `C:\Program Files\PostgreSQL\16\bin` pridaj do `PATH` - appka
potrebuje `pg_dump` na zálohy.

## 2. Appka

```bash
git clone <tvoj-repozitar> /opt/shift_planner
cd /opt/shift_planner
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

(Windows: `python -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt`,
`copy .env.example .env`.)

V `.env` nastav (ostatné nechaj):

```
SECRET_KEY=<výstup z: python -c "import secrets; print(secrets.token_hex(32))">
DATABASE_URL=postgresql://planner:ZMEN-TO-HESLO@127.0.0.1:5432/shift_planner
BEHIND_PROXY=1
SESSION_COOKIE_SECURE=1
```

> `BEHIND_PROXY=1` zapni, keď je pred appkou Caddy. Appka (waitress)
> potom prijíma hlavičky `X-Forwarded-*` (skutočná IP klienta, HTTPS) len
> od proxy na adrese `127.0.0.1`; ak proxy beží na inom serveri, nastav
> `TRUSTED_PROXY=<jeho adresa>`. Hlavičky od čohokoľvek iného sa zahodia.
> Bez `BEHIND_PROXY=1` by appka videla všetkých klientov ako adresu
> proxy - a ochrana prihlásenia (nižšie) by ich zablokovala naraz.

## 3. Databáza

### Nová inštalácia (bez starých dát)

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py create-admin
```

### Prechod z doterajšej SQLite databázy (so zachovaním dát)

Urob to v tomto poradí. Pôvodný SQLite súbor ostane nezmenený - ponechaj
si ho ako zálohu.

1. **SQLite na najnovšiu verziu schémy** - dočasne vypni `DATABASE_URL`
   (riadok v `.env` zakomentuj `#`) a spusti:

   ```bash
   .venv/bin/python manage.py migrate
   ```

2. **Zapni PostgreSQL** - riadok `DATABASE_URL=...` v `.env` odkomentuj a:

   ```bash
   .venv/bin/python manage.py migrate        # vytvorí prázdnu schému v PostgreSQL
   .venv/bin/python manage.py import-sqlite  # skopíruje dáta zo SQLite
   ```

   Príkaz vypíše počty skopírovaných riadkov. Je bezpečný: beží v jednej
   transakcii (pri chybe sa nič nezmení) a odmietne pokračovať, ak
   cieľová databáza už obsahuje dáta. Súbor inde než v predvolenej
   ceste zadáš ako argument: `import-sqlite C:\cesta\shift_planner.db`.

3. Skontroluj: `.venv/bin/python manage.py status`

## 4. Skúšobné spustenie

```bash
.venv/bin/python manage.py serve
```

Server beží na `http://127.0.0.1:8000`. Odmietne štart, ak nie je
nastavený vlastný `SECRET_KEY`. Voliteľne: `HOST`, `PORT`, `THREADS`
(predvolene `127.0.0.1`, `8000`, `8`).

## 5. HTTPS (Caddy)

Inštalácia: https://caddyserver.com/docs/install

Vzorová konfigurácia je v `deploy/Caddyfile` (zmeň doménu):

```
plan.firma.sk {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
```

Linux: skopíruj do `/etc/caddy/Caddyfile` a `sudo systemctl reload caddy`.
Po chvíli otvor `https://plan.firma.sk`.

## 6. Trvalý beh (aby appka bežala po reštarte)

### Linux (systemd)

```bash
sudo useradd --system --home /opt/shift_planner shiftplanner
sudo chown -R shiftplanner: /opt/shift_planner
sudo cp deploy/shift-planner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shift-planner
sudo systemctl status shift-planner
```

Logy: `journalctl -u shift-planner -f`

### Windows (NSSM)

Stiahni NSSM (nssm.cc) a v PowerShelli ako správca:

```powershell
nssm install ShiftPlanner "C:\shift_planner\.venv\Scripts\python.exe" "manage.py serve"
nssm set ShiftPlanner AppDirectory "C:\shift_planner"
nssm start ShiftPlanner
```

Caddy rovnako (alebo podľa jeho návodu pre Windows službu):

```powershell
nssm install Caddy "C:\caddy\caddy.exe" "run --config C:\caddy\Caddyfile"
nssm start Caddy
```

## 7. Zálohy

Záloha sa robí cez `pg_dump` do `app/data/backups/*.dump`:

```bash
.venv/bin/python manage.py backup
.venv/bin/python manage.py list-backups
```

Automaticky každú noc (Linux, `crontab -e` pod používateľom appky) vrátane
mazania záloh starších ako 30 dní:

```
0 2 * * * cd /opt/shift_planner && .venv/bin/python manage.py backup && find app/data/backups -name "*.dump" -mtime +30 -delete
```

Windows: Plánovač úloh (Task Scheduler) s akciou
`C:\shift_planner\.venv\Scripts\python.exe` a argumentmi `manage.py backup`,
začať v `C:\shift_planner`.

Zálohy patria **aj mimo servera** (iný disk/cloud) - záloha na tom istom
disku nepomôže pri jeho výpadku. Admin si môže zálohu stiahnuť aj v appke
(menu "Zálohovať databázu").

**Obnova** je zámerne ručná (prepíše všetky dáta).
`python manage.py restore <subor.dump>` vypíše presný príkaz
`pg_restore`, ktorý treba spustiť (po zastavení appky).

## 8. Aktualizácia appky

```bash
cd /opt/shift_planner
git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate      # pred migráciou sa sama urobí záloha
sudo systemctl restart shift-planner
```

`migrate` na PostgreSQL pred zmenou schémy automaticky zálohuje cez
`pg_dump`. Ak `pg_dump` nie je k dispozícii, **migráciu nespustí**
(ochrana dát); na vlastné riziko ju vieš spustiť s `--no-backup`.

## Ochrana prihlásenia

- Po **5 neúspešných pokusoch** o prihlásenie s tým istým menom z tej
  istej IP adresy sa táto dvojica zablokuje na **15 minút** (aj so
  správnym heslom - inak by sa dalo hádať ďalej). Skutočný používateľ sa
  z inej adresy prihlási bez problémov, takže cudzí človek mu účet
  nezamkne.
- Po **20 neúspešných pokusoch** z jednej IP adresy (s ľubovoľnými
  menami) sa zablokuje celá adresa na 15 minút.
- Neexistujúce meno sa správa rovnako ako existujúce (rovnaká správa aj
  čas odpovede), takže sa nedá zistiť, ktoré mená existujú.
- Rovnako sa limituje hádanie aktuálneho hesla pri zmene hesla.
- Každý neúspešný pokus a zablokovanie sa zapíše do logu
  (`journalctl -u shift-planner -f`) s menom a IP adresou, bez hesla.

Limity sa nastavujú v `.env`: `LOGIN_MAX_ATTEMPTS` (5),
`LOGIN_IP_MAX_ATTEMPTS` (20), `LOGIN_LOCKOUT_MINUTES` (15).

Počítadlá sú v pamäti appky: po jej reštarte sa vynulujú (blokovanie sa
len uvoľní). Ak človek zabudne heslo a zablokuje sa, stačí počkať,
alebo admin reštartuje appku / resetuje heslo.

## Kontrolný zoznam

- [ ] `SECRET_KEY` je vlastný, náhodný (nie z `.env.example`)
- [ ] `DATABASE_URL` mieri na PostgreSQL, heslo databázy je silné
- [ ] waitress počúva len na `127.0.0.1`, verejne je dostupný len Caddy
- [ ] `BEHIND_PROXY=1` a `SESSION_COOKIE_SECURE=1`
- [ ] v logu pri neúspešnom prihlásení je skutočná IP klienta (nie `127.0.0.1`)
- [ ] `https://...` funguje a `http://...` presmeruje na HTTPS (Caddy to robí sám)
- [ ] firewall: otvorené len 80 a 443 (PostgreSQL port 5432 nie je z internetu dostupný)
- [ ] nočná záloha beží a zálohy sa kopírujú mimo servera
- [ ] vytvorený admin účet; predvolené/testovacie účty zrušené

## Testovanie proti PostgreSQL (pre vývoj)

Celú sadu testov vieš spustiť aj nad PostgreSQL - použi **prázdnu
testovaciu databázu** (pred každým testom sa v nej zmaže všetko!):

```powershell
$env:TEST_DATABASE_URL = "postgresql://planner:heslo@localhost:5432/shift_planner_test"
python -m pytest -q
```

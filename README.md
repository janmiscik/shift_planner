# Shift Planner

Webová aplikácia (Flask) na plánovanie pracovných zmien zamestnancov.

## Funkcie

- evidencia zamestnancov a oddelení (aj priradenie zamestnanca
  k viacerým oddeleniam s rozdelením pracovného fondu),
- vytváranie a úprava pracovných smien,
- automatická kontrola pri ukladaní smeny:
  - zamedzenie prekrývania smien toho istého zamestnanca (aj cez
    polnoc - nočné smeny),
  - kontrola týždenného pracovného fondu zamestnanca,
  - zamestnanca je možné priradiť len na oddelenie, ku ktorému má
    aktívnu väzbu,
  - zohľadnenie schválených absencií (dovolenka, PN, OČR, náhradné
    voľno),
- upozornenie na nedostatočné obsadenie smeny (minimálny počet ľudí
  na oddelenie),
- interaktívny kalendár (FullCalendar) s presúvaním smien
  metódou drag-and-drop, filtrom podľa oddelenia/zamestnanca,
- export harmonogramu do Excelu (.xlsx), PDF a iCalendar (.ics -
  zamestnanec si svoje smeny vie naimportovať do Google Kalendára
  alebo Outlooku cez `/employees/<id>/export.ics`),
- CSRF ochrana a backendová validácia formulárov (Flask-WTF),
- viac pobočiek (každé oddelenie a zamestnanec patrí do jednej
  pobočky) a tri role - administrátor (všetko), manažér (smeny a
  absencie na pridelených pobočkách) a zamestnanec (vidí a exportuje
  len svoje smeny),
- tmavý/svetlý režim appky,
- zálohovanie databázy (automaticky pred migráciou, aj ručne jedným
  klikom v appke).

## Inštalácia

1. Vytvor a aktivuj virtuálne prostredie:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

2. Nainštaluj závislosti:

   ```powershell
   pip install -r requirements.txt
   ```

3. Priprav databázu (spustí všetky migrácie):

   ```powershell
   python manage.py migrate
   ```

4. Vytvor si administrátorský účet (nutné, inak sa do appky nedostaneš):

   ```powershell
   python manage.py create-admin
   ```

   Opýta sa na používateľské meno a heslo (heslo sa pri písaní
   nezobrazuje). Ak aktualizuješ staršiu inštaláciu, existujúci
   manažéri sa pri migrácii automaticky stanú administrátormi (mali
   plný prístup) a všetky dáta sa presunú do pobočky "Hlavná pobočka".

## Pobočky, prihlasovanie a role

Appka vyžaduje prihlásenie. Členenie dát: **pobočka -> oddelenia ->
zamestnanci -> smeny**. Zamestnanec patrí do jednej pobočky (po
vytvorení sa nedá presunúť) a jeho smeny, absencie aj oddelenia sú v
tej istej pobočke. Názov oddelenia musí byť jedinečný len v rámci
pobočky.

| Rola | Čo smie |
|---|---|
| **administrátor** | všetko: pobočky (`/branches`), účty adminov a manažérov (`/users`), zamestnanci, oddelenia, zálohy, všetky pobočky |
| **manažér** | vytvára, upravuje a maže smeny a absencie, kalendár a exporty a schvaľuje žiadosti o voľno - **len na pobočkách, ktoré mu admin pridelil**. Zamestnancov a oddelenia vidí len na čítanie |
| **zamestnanec** | len vlastný (zverejnený) rozpis (`/my-schedule`) s exportom `.xlsx`/`.pdf`/`.ics` a žiadosti o voľno (`/my-requests`). Smeny ostatných nevidí |

Voliteľne sa dá zamestnancom zapnúť aj kalendár svojej pobočky len na
čítanie (`EMPLOYEES_CAN_VIEW_BRANCH_CALENDAR=1` v `.env`); predvolene je
vypnutý.

Prihlasovacie údaje pre zamestnanca sa vytvárajú **automaticky** pri
jeho pridaní (`/employees/add`) - vyplníš mu meno/heslo a vyberieš
pobočku priamo v tom istom formulári. Existujúcemu zamestnancovi bez
účtu ich vieš dodatočne vytvoriť na jeho stránke úpravy
(`/employees/edit/<id>`), tam si aj vieš resetovať heslo alebo účet
zrušiť.

**Zamestnanec s funkciou manažéra (jeden účet):** na stránke úpravy
zamestnanca (`/employees/edit/<id>`) je sekcia "Funkcia manažér" -
zapneš ju a zaškrtneš pobočky, v ktorých smie plánovať smeny. Ten istý
účet potom plánuje v týchto pobočkách a popri tom si pozerá vlastný
rozpis (menu "Môj rozpis"). Zamestnanec musí mať prihlasovací účet.
Funkciu môže prideliť/odobrať len admin.

Čistých manažérov (bez zamestnaneckého účtu) a ďalších adminov vytvoríš
v appke na stránke **Účty**
(`/users`, výber pobočiek pri manažérovi sa dá kedykoľvek zmeniť),
alebo príkazmi `python manage.py create-admin` a
`python manage.py create-manager`. Každý si môže zmeniť vlastné heslo
cez "Zmeniť heslo" v menu.

Prihlasovanie je chránené pred hádaním hesiel: po 5 neúspešných
pokusoch sa dvojica (IP, meno) na 15 minút zablokuje (limity sa dajú
nastaviť v `.env`, pozri `DEPLOYMENT.md`).

Oprávnenia routov určujú zoznamy `_*_ALLOWED_ENDPOINTS` v
`app/web/app.py` (pre manažéra a zamestnanca platí zoznam povolených
routov, nová routa je teda predvolene len pre admina). Test
`test_every_endpoint_has_an_explicit_permission_decision` zlyhá, kým
pri novej route nerozhodneš, kto ju smie používať.

## Výmena smien

Zamestnanec (aj manažér, ktorý je zároveň zamestnancom) v menu **Výmeny
smien** (`/my-swaps`) navrhne kolegovi z tej istej pobočky, aby prevzal
jeho smenu:

- **odovzdanie** - kolega smenu len prevezme, alebo
- **výmena** - kolega dá na oplátku jednu zo **svojich** smien (vyberie
  si ju sám, lebo cudzie smeny zamestnanci nevidia).

Priebeh: navrhne zamestnanec -> **kolega prijme alebo odmietne** ->
**manažér (alebo admin) schváli alebo zamietne** (zamietnutie vyžaduje
dôvod). Až po schválení sa smeny prehodia; dovtedy sa nič nemení. Každý z
dvoch zamestnancov môže otvorenú žiadosť zrušiť.

- Vymeniť sa dajú len **budúce zverejnené** smeny (koncepty zamestnanec
  nevidí) a smena môže byť naraz len v jednej otvorenej žiadosti.
  Kolega musí byť v tej istej pobočke, aktívny a mať prihlasovací účet.
- Pri schválení sa **nové pridelenie overí rovnakými pravidlami ako pri
  ručnej úprave smeny**: prekrytie, odpočinok, týždenné fondy, absencia a
  priradenie k oddeleniu - pre oboch zamestnancov. Ak čokoľvek nesedí,
  nezmení sa nič (ani polovica výmeny) a manažér uvidí presný dôvod.
  Ak sa smena od odpovede kolegu zmenila (zmazaná, stiahnutá do konceptu,
  zamestnanec deaktivovaný), schválenie sa zablokuje.
- Manažér vidí žiadosti zo svojich pobočiek v menu **Výmeny smien**
  (`/swap-requests`) s počtom čakajúcich. Výmenu, v ktorej je sám
  zainteresovaný, vybaví iný manažér alebo admin. Kolega vidí počet
  žiadostí čakajúcich na jeho odpoveď.
- Všetko sa zapisuje do záznamov zmien (aj samotné prehodenie smien).

## Mesačný prehľad hodín (podklad pre mzdy)

Menu **Prehľad hodín** (`/reports/hours`, manažér a admin, v rámci svojich
pobočiek): vyberieš pobočku (alebo všetky), mesiac a rok a uvidíš pre
každého zamestnanca:

- počet smien a odpracované **hodiny**, z toho **nočné** (22:00-06:00),
  **sobotné**, **nedeľné** a hodiny počas **štátneho sviatku**,
- **pracovné dni absencií** (dovolenka, PN, OČR, náhradné voľno),
- súčty za všetkých a počet pracovných dní v mesiaci.

Voliteľne len zverejnené smeny (inak sa počítajú aj koncepty). Export do
**.xlsx** (listy Prehľad, Smeny - podklad ku každej smene, Poznámky k
výpočtu) a do **.csv** (UTF-8 s BOM, oddeľovač `;`, desatinná čiarka -
otvorí sa v Exceli).

Pravidlá výpočtu:
- smena patrí do mesiaca podľa dátumu **začiatku** (nočná smena z
  posledného dňa mesiaca sa počíta celá do tohto mesiaca),
- hodiny sú dĺžka smeny **bez odpočítania prestávok**,
- sobotné/nedeľné/sviatočné hodiny sa určujú podľa skutočných
  kalendárnych dní, cez ktoré smena prechádza (nočná z piatka na sobotu
  má sobotné hodiny po polnoci); sobota/nedeľa a sviatok sa môžu
  prekrývať,
- štátne sviatky SR sú zabudované (vrátane Veľkého piatku a
  Veľkonočného pondelka), pracovný deň = pondelok až piatok mimo sviatkov.

Prehľad je podklad - výsledné mzdové nároky určuje mzdová účtovníčka.

## Záznamy zmien (audit log)

Appka zapisuje, **kto, kedy a čo zmenil**. Záznamy sú v menu **Záznamy
zmien** (`/audit`) pre manažéra a admina, s filtrom podľa pobočky, typu,
používateľa a dátumu a s exportom do CSV (otvorí sa v Exceli).

- Zachytáva sa **automaticky** každá zmena smien (pridanie, úprava časov,
  presun v kalendári, zmazanie), absencií, žiadostí o voľno (podanie,
  schválenie, zamietnutie), zamestnancov, oddelení, priradení k
  oddeleniam, šablón, pobočiek a účtov (vrátane zmeny role, pobočiek a
  hesla). Pri úpravách je vidno pôvodnú aj novú hodnotu (napr. `začiatok:
  06:00 → 07:00`). **Heslá ani ich hashe sa nikdy nezapisujú.**
- Hromadné akcie sú jeden súhrnný záznam: zverejnenie/stiahnutie týždňa,
  kopírovanie týždňa a použitie šablóny (nie záznam za každú smenu).
  Zálohu stiahnutú adminom appka tiež zapíše.
- Manažér vidí záznamy len zo svojich pobočiek; záznamy o účtoch a
  pobočkách vidí iba admin. Zamestnanec záznamy nevidí.
- Zmeny urobené mimo webu (`manage.py`) sú pod používateľom "systém".
  Meno používateľa ostane v starých záznamoch aj po zmazaní jeho účtu.
- Časy sa zobrazujú v lokálnom čase (`APP_TIMEZONE`, predvolene
  `Europe/Bratislava`).
- Záznamy sa nemažú samé. Staré vieš zmazať príkazom
  `python manage.py audit-prune <dni>` (len záznamy staršie ako 30 dní).
- Audit nikdy nezhodí samotnú operáciu: ak sa záznam nepodarí vytvoriť,
  chyba sa len zapíše do logu servera.

## Žiadosti o voľno

Zamestnanec (aj manažér, ktorý je zároveň zamestnancom) podáva žiadosť v
menu **Žiadosti o voľno** (`/my-requests`): druh (dovolenka alebo
náhradné voľno), obdobie od-do a voliteľná poznámka. PN a OČR žiadosťou
nevybavuje - zapisuje ich manažér medzi absencie.

- Žiadosť nemožno podať spätne (začiatok je dnes alebo neskôr), ani na
  obdobie, kde už má zamestnanec absenciu alebo čakajúcu žiadosť.
  Čakajúcu žiadosť môže sám zrušiť.
- Manažér/admin vidí žiadosti zo svojich pobočiek v menu **Žiadosti o
  voľno** (`/leave-requests`) s počtom čakajúcich v menu. Pri každej vidí
  aj to, koľko smien má zamestnanec v danom období naplánovaných.
- **Schválenie** vytvorí bežnú absenciu - kalendár ju zobrazí a
  plánovanie smien na ten deň ju rešpektuje. Po schválení appka upozorní,
  ak zamestnanec v období ešte má naplánované smeny. **Zamietnutie**
  vyžaduje dôvod, ktorý zamestnanec uvidí.
- Vlastnú žiadosť si manažér nemôže schváliť - vybaví ju iný manažér alebo
  admin.
- Upozornenia e-mailom zatiaľ nie sú: zamestnanec vidí stav žiadosti na
  svojej stránke, manažér počet čakajúcich v menu.

## Zverejnenie rozpisu (koncept / zverejnené)

Nové smeny (pridané ručne, skopírované aj z šablóny) sú **koncept** -
vidí ich len manažér a admin (na stránke Smeny so stavom "Koncept", v
kalendári prerušovane a priesvitne). Zamestnanec vidí vo svojom rozpise,
exportoch (`.xlsx`, `.pdf`, `.ics`) aj v kalendári pobočky len
**zverejnené** smeny.

Manažér zverejňuje na stránke **Smeny -> Zverejniť týždeň**
(`/shifts/publish`): vyberie pobočku a týždeň, uvidí počet konceptov a
zverejní ich jedným tlačidlom (alebo zverejnenie stiahne späť).

- Úprava alebo presun už zverejnenej smeny zostáva zverejnená (zamestnanec
  vidí zmenu hneď, smena mu nezmizne).
- Smena pridaná do už zverejneného týždňa je opäť koncept, kým sa týždeň
  nezverejní znova.
- Doterajšie smeny sa pri aktualizácii (`migrate`) označia ako zverejnené,
  aby o svoj rozpis nikto neprišiel.
- Ak nechceš dvojkrokové plánovanie, nastav v `.env`
  `AUTO_PUBLISH_SHIFTS=1` - nové smeny sa potom zverejnia hneď.

## Kopírovanie týždňa a šablóny smien

Na stránke **Smeny** (manažér a admin, v rámci svojich pobočiek):

- **Kopírovať týždeň** (`/shifts/copy-week`) - vyber pobočku, zdrojový a
  cieľový týždeň (stačí ľubovoľný deň v ňom). Najprv sa zobrazí náhľad,
  nič sa neuloží, kým nepotvrdíš.
- **Šablóny** (`/shifts/templates`) - uloží smeny jednej pobočky z
  vybraného týždňa ako pomenovanú šablónu, ktorú potom použiješ na
  ľubovoľný ďalší týždeň (opäť s náhľadom). Názov šablóny je jedinečný v
  rámci pobočky.

Každá kopírovaná smena prejde rovnakými pravidlami ako ručne pridaná
(prekrytie, odpočinok, týždenné fondy, absencie, oddelenie). Skopírované
smeny sú koncept (pozri vyššie). Smena, ktorá
by pravidlo porušila, sa **preskočí a uvedie sa dôvod** - ostatné sa
vytvoria. Preskočí sa aj smena neaktívneho zamestnanca a smena, ktorá v
cieľovom týždni už je. Opakované kopírovanie preto nevytvára duplicity.

## Nasadenie pre pobočky

Pre reálnu prevádzku s viacerými pobočkami (PostgreSQL, produkčný
server, HTTPS, zálohy, presun dát zo SQLite) pozri
[`DEPLOYMENT.md`](DEPLOYMENT.md). Produkčný server sa spúšťa
`python manage.py serve`.

## Štruktúra kódu

```
app/
  web/
    app.py          zostavenie appky (create_app)
    core.py         blueprint "main" a CSRF
    permissions.py  oprávnenia podľa rolí, kontrola prihlásenia
    helpers.py      rozsah pobočiek a spoločné pomocné funkcie
    forms.py        formuláre
    routes/         routy podľa oblastí (auth, employees, shifts, planning,
                    leave_requests, reports, audit, absences, admin, ...)
    templates/      šablóny
  services/         biznis logika (pravidlá smien, exporty, audit, ...)
  orm_models.py     databázové modely
migrations/         Alembic migrácie
tests/              testy
```

Všetky routy sú v jednom blueprinte `main`, takže názov endpointu je
`main.<názov_funkcie>`. Novú routu pridaj do príslušného modulu v
`app/web/routes/` a **rozhodni, kto ju smie používať** v
`app/web/permissions.py` (predvolene je prístupná len adminovi).

## Automatické testy (GitHub Actions)

Pri každom pushi a pull requeste sa na GitHube spustia všetky testy
(`.github/workflows/tests.yml`): raz na SQLite a raz na PostgreSQL.
Výsledok vidno pri commite a na záložke Actions.

## Spustenie

```powershell
python manage.py runserver
```

Aplikácia beží na `http://127.0.0.1:5000`. Premennými `HOST`, `PORT` a
`FLASK_DEBUG` sa dá server nastaviť; pri inom hoste než localhost je
debug vždy vypnutý (Werkzeug debugger by inak umožnil spúšťať kód).
Pri nasadení za HTTPS nastav aj `SESSION_COOKIE_SECURE=1`.

### SECRET_KEY (produkcia)

Appka beží aj bez nastavenej premennej `SECRET_KEY` (na vývoj), ale
vypíše varovanie a použije predvolený, verejne známy kľúč - to nie je
bezpečné pre nasadenie mimo tvojho počítača. Pred produkčným nasadením
si vygeneruj vlastný a ulož ho do súboru `.env` (skopíruj z
`.env.example`, súbor `.env` sa necommituje do gitu):

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
copy .env.example .env
# do .env vlož vygenerovaný reťazec ako SECRET_KEY=...
python manage.py runserver
```

## Testy

```powershell
pytest
```

## Štruktúra projektu

```
app/
  extensions.py  - zdieľané rozšírenia (SQLAlchemy `db`, Flask-Migrate)
  orm_models.py  - SQLAlchemy modely (Employee, Department,
                   EmployeeDepartment, Shift) a ich vzťahy
  data/          - cesta k SQLite súboru
  models/        - staršie jednoduché dátové modely (nepoužívané)
  services/      - biznis logika (validácie, kalendár, export) -
                   pristupuje k DB cez SQLAlchemy, vracia dáta
                   v rovnakom tvare (n-tice) ako predtým, aby
                   šablóny a testy fungovali bez zmien
  web/           - Flask aplikácia: `create_app()` factory (app.py),
                   formuláre (forms.py), šablóny a statické súbory
migrations/      - Alembic migračné skripty (Flask-Migrate)
tests/           - pytest testy (Flask test client + dočasná DB,
                   vlastná appka pre každý test cez `create_app()`)
manage.py        - CLI: migrate / runserver / cleanup-duplicates / ...
```

### Prečo tuply, nie ORM objekty v šablónach?

Service funkcie interne používajú skutočné SQLAlchemy modely a
vzťahy (napr. M:N medzi zamestnancom a oddelením cez
`employee.departments`), ale navonok vracajú dáta v rovnakom tvare
(n-tice/`Row`), ako predtým s ručným SQL. Vďaka tomu prechod na ORM
nevyžadoval prepísať všetky šablóny a testy naraz - ak by si chcel aj
šablóny prepísať na prácu priamo s ORM objektmi (`shift.employee.first_name`
namiesto `shift[1]`), dá sa to urobiť ako samostatný, menší krok.

## Databázové migrácie (SQLAlchemy + Alembic)

Dátová vrstva beží na SQLAlchemy (modely v `app/orm_models.py`),
schéma sa spravuje cez Alembic (`Flask-Migrate`). Migračné skripty sú
v `migrations/versions/`.

```powershell
python manage.py migrate
```

Tento príkaz sám rozozná, v akom stave je tvoja databáza:
- **úplne nová** databáza -> vytvorí všetky tabuľky od začiatku,
- **staršia databáza** (z verzie appky spred prechodu na Alembic,
  spravovanej ručným migračným runnerom) -> jej schéma je už
  aktuálna, príkaz ju len "označí" ako spravovanú Alembicom
  (Alembic stamp) - **žiadne dáta sa nemenia ani nemažú**,
- databáza **už spravovaná Alembicom** -> normálna aktualizácia na
  najnovšiu migráciu.

Je preto bezpečné spúšťať ho opakovane, kedykoľvek, aj na už
existujúcej databáze s dátami.

Ak v budúcnosti zmeníš modely v `app/orm_models.py`, novú migráciu
vygeneruješ takto:

```powershell
$env:FLASK_APP = "app.web.app:create_app"
flask db migrate -m "popis zmeny"
python manage.py migrate
```

## Zákonník práce - odpočinok a strop hodín

Pri ukladaní smeny sa navyše kontroluje:

- **minimálny odpočinok medzi dvomi po sebe idúcimi smenami** -
  predvolene 12 hodín (Zákonník práce § 92). Dá sa zmeniť cez
  premennú prostredia `MIN_REST_HOURS` (napr. na 11, ak to vyhovuje
  vašej prevádzke/dohode),
- **zákonný strop týždenného pracovného času vrátane nadčasov** -
  predvolene 48 hodín (Zákonník práce § 97), nastaviteľné cez
  `MAX_WEEKLY_HOURS_WITH_OVERTIME`. Tento strop platí VŽDY, aj keď
  má zamestnanec v profile nastavený vyšší osobný fond hodín.

Obe hodnoty nastavíš v súbore `.env`:

```
MIN_REST_HOURS=12
MAX_WEEKLY_HOURS_WITH_OVERTIME=48
```

## Export smien konkrétneho zamestnanca (.ics)

Na stránke úpravy zamestnanca (`/employees/edit/<id>`) je tlačidlo
"⬇ Export jeho zmien (.ics)" - stiahne len smeny TOHTO zamestnanca
vo formáte, ktorý sa dá priamo naimportovať/prihlásiť na odber v
Google Kalendári, Outlooku alebo Apple Kalendári.

## Zálohovanie databázy

**Nikdy nemaž celý priečinok projektu, ak v ňom máš aj
`app/data/shift_planner.db`** - ten súbor obsahuje všetky tvoje
reálne dáta a nie je súčasťou zipu, ktorý dostaneš pri aktualizácii
appky. Pri aktualizácii kódu vždy nahrádzaj len súbory appky, nikdy
nemaž priečinok `app/data/` (alebo si databázu najprv zálohuj mimo
projektu).

Appka teraz zálohuje automaticky aj ručne:

- **Automaticky** pred každým `python manage.py migrate` (do
  `app/data/backups/`).
- **Ručne** z príkazového riadku:
  ```powershell
  python manage.py backup
  python manage.py list-backups
  python manage.py restore <nazov_suboru.db>
  ```
- **Jedným klikom v appke** - odkaz "💾 Zálohovať databázu" v bočnom
  menu stiahne aktuálnu databázu ako súbor do prehliadača (a zároveň
  ju uloží aj do `app/data/backups/` na serveri). Toto je najlepší
  spôsob, ako si urobiť kópiu niekam MIMO priečinka projektu (na
  Plochu, USB kľúč, cloud) - napr. pred väčšou aktualizáciou appky.

Zálohy sa neukladajú do gitu (`app/data/backups/` je v
`.gitignore`).

## Kapacita oddelenia

Pri oddelení môžeš nastaviť "Minimálny počet ľudí na zmene". Ak je
na konkrétny deň/typ smeny v danom oddelení naplánovaných menej ľudí,
appka na to upozorní v kalendári (oranžový panel nad mriežkou).

## Absencie (dovolenka, PN, OČR, náhradné voľno)

Stránka `/absences` - pridávanie, úprava a mazanie neprítomností
zamestnancov. Appka automaticky zohľadňuje schválené absencie pri
plánovaní smien: zamestnancovi sa nedá pridať/presunúť smena na deň,
kedy má neprítomnosť. Naopak, ak pridáš absenciu na obdobie, kedy už
má naplánované smeny, appka ťa na to len upozorní (nezablokuje) -
rozhodnutie necháva na tebe.

## Filter podľa oddelenia v kalendári

Filter zohľadní aj smeny, ktoré nemajú oddelenie nastavené priamo
(pole je pri pridávaní smeny nepovinné) - v takom prípade sa pozrie,
či je zamestnanec k danému oddeleniu priradený. Ak máš staršie smeny
bez oddelenia a chceš im ho doplniť natrvalo (nie len pri filtrovaní),
spusti jednorazovo:

```powershell
python manage.py backfill-departments
```

Doplní oddelenie len tam, kde je to jednoznačné (zamestnanec je
priradený presne k jednému oddeleniu). Zvyšok treba doplniť ručne cez
úpravu smeny v appke.

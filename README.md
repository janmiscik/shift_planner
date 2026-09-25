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
- prihlasovanie s dvomi rolami - manažér (plný prístup) a zamestnanec
  (len na čítanie vlastného rozpisu),
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

4. Vytvor si manažérsky účet (nutné, inak sa do appky nedostaneš):

   ```powershell
   python manage.py create-manager
   ```

   Opýta sa na používateľské meno a heslo (heslo sa pri písaní
   nezobrazuje).

## Prihlasovanie a role

Appka teraz vyžaduje prihlásenie. Sú dve role:

- **manažér** - plný prístup ku všetkému (presne to, čo appka robila
  doteraz),
- **zamestnanec** - prístup len na čítanie vlastného rozpisu
  (`/my-schedule`) a exportu vlastných zmien do `.ics`. Nevidí iných
  zamestnancov, oddelenia, ani nemôže nič upravovať.

Prihlasovacie údaje pre zamestnanca sa vytvárajú **automaticky** pri
jeho pridaní (`/employees/add`) - vyplníš mu meno/heslo priamo v tom
istom formulári. Existujúcemu zamestnancovi bez účtu ich vieš
dodatočne vytvoriť na jeho stránke úpravy (`/employees/edit/<id>`),
tam si aj vieš resetovať heslo alebo účet zrušiť.

Ďalších manažérov (okrem prvého) vytvoríš rovnako cez
`python manage.py create-manager`.

## Spustenie

```powershell
python manage.py runserver
```

Aplikácia beží na `http://127.0.0.1:5000`.

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

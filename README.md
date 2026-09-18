# Shift Planner

Webová aplikácia (Flask) na plánovanie pracovných zmien zamestnancov.

## Funkcie

- evidencia zamestnancov a oddelení (aj priradenie zamestnanca
  k viacerým oddeleniam s rozdelením pracovného fondu),
- vytváranie a úprava pracovných smien,
- automatická kontrola pri ukladaní smeny:
  - zamedzenie prekrývania smien toho istého zamestnanca,
  - kontrola týždenného pracovného fondu zamestnanca,
  - zamestnanca je možné priradiť len na oddelenie, ku ktorému má
    aktívnu väzbu,
- interaktívny kalendár (FullCalendar) s presúvaním smien
  metódou drag-and-drop,
- export harmonogramu do Excelu (.xlsx) aj do PDF,
- CSRF ochrana a backendová validácia formulárov (Flask-WTF).

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

## Spustenie

```powershell
python manage.py runserver
```

Aplikácia beží na `http://127.0.0.1:5000`.

## Testy

```powershell
pytest
```

## Štruktúra projektu

```
app/
  data/          - pripojenie k DB a migrácie (app/data/migrations.py)
  models/        - jednoduché dátové modely
  services/      - biznis logika (validácie, kalendár, export) - bez Flasku
  web/           - Flask aplikácia: routes (app.py), formuláre (forms.py),
                   šablóny a statické súbory
tests/           - pytest testy (Flask test client + dočasná DB)
manage.py        - CLI: migrate / runserver
```

## Databázové migrácie

Namiesto viacerých samostatných skriptov je schéma spravovaná cez
jeden runner (`app/data/migrations.py`). Každá migrácia sa eviduje
v tabuľke `schema_migrations` a spustí sa len raz - `python manage.py
migrate` je preto bezpečné spúšťať opakovane, aj na už existujúcej
databáze.

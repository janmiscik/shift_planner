"""Flask-WTF formuláre - CSRF ochrana a backendová validácia vstupov."""

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    FloatField,
    IntegerField,
    PasswordField,
    SelectField,
    SelectMultipleField,
    StringField,
    TimeField,
)
from wtforms.validators import (
    DataRequired,
    EqualTo,
    Length,
    NumberRange,
    Optional,
)

from app.services.leave_request_service import LEAVE_REQUEST_TYPES


class BranchForm(FlaskForm):
    name = StringField(
        "Názov pobočky",
        validators=[
            DataRequired(message="Zadaj názov pobočky."),
            Length(max=100),
        ],
    )


class EmployeeForm(FlaskForm):
    # Pobočka sa vyberá len pri pridaní (admin). Prázdna hodnota =
    # predvolená pobočka. Choices sa dopĺňajú v route.
    branch_id = SelectField(
        "Pobočka",
        validators=[Optional()],
        choices=[],
    )
    first_name = StringField(
        "Meno",
        validators=[DataRequired(message="Zadaj meno."), Length(max=100)],
    )
    last_name = StringField(
        "Priezvisko",
        validators=[
            DataRequired(message="Zadaj priezvisko."),
            Length(max=100),
        ],
    )
    position = StringField(
        "Pozícia",
        validators=[DataRequired(message="Zadaj pozíciu."), Length(max=100)],
    )
    weekly_hours = FloatField(
        "Týždenný pracovný fond",
        validators=[
            DataRequired(message="Zadaj týždenný pracovný fond."),
            NumberRange(
                min=0.5,
                max=168,
                message="Fond musí byť medzi 0.5 a 168 hodinami.",
            ),
        ],
    )


class DepartmentForm(FlaskForm):
    # Pobočka sa vyberá len pri pridaní (admin). Prázdna hodnota =
    # predvolená pobočka. Choices sa dopĺňajú v route.
    branch_id = SelectField(
        "Pobočka",
        validators=[Optional()],
        choices=[],
    )
    name = StringField(
        "Názov oddelenia",
        validators=[
            DataRequired(message="Zadaj názov oddelenia."),
            Length(max=100),
        ],
    )
    min_staff = IntegerField(
        "Minimálny počet ľudí na zmene",
        validators=[
            Optional(),
            NumberRange(
                min=1,
                max=500,
                message="Zadaj kladné číslo (alebo nechaj prázdne).",
            ),
        ],
    )


class EmployeeDepartmentForm(FlaskForm):
    department_id = SelectField(
        "Oddelenie",
        validators=[DataRequired(message="Vyber oddelenie.")],
        coerce=int,
    )
    weekly_hours = FloatField(
        "Hodiny za týždeň",
        validators=[
            DataRequired(message="Zadaj počet hodín za týždeň."),
            NumberRange(
                min=0.5,
                max=168,
                message="Hodnota musí byť medzi 0.5 a 168 hodinami.",
            ),
        ],
    )


SHIFT_TYPE_CHOICES = [
    ("Ranná", "Ranná"),
    ("Medzismena", "Medzismena"),
    ("Poobedná", "Poobedná"),
    ("Nočná", "Nočná"),
]


class ShiftForm(FlaskForm):
    employee_id = SelectField(
        "Zamestnanec",
        validators=[DataRequired(message="Vyber zamestnanca.")],
        coerce=int,
    )
    department_id = SelectField(
        "Oddelenie",
        validators=[Optional()],
        choices=[],
    )
    shift_date = DateField(
        "Dátum",
        validators=[DataRequired(message="Zadaj dátum.")],
    )
    start_time = TimeField(
        "Začiatok smeny",
        validators=[DataRequired(message="Zadaj začiatok smeny.")],
    )
    end_time = TimeField(
        "Koniec smeny",
        validators=[DataRequired(message="Zadaj koniec smeny.")],
    )
    shift_type = SelectField(
        "Typ smeny",
        choices=SHIFT_TYPE_CHOICES,
        validators=[DataRequired(message="Vyber typ smeny.")],
    )


ABSENCE_TYPE_CHOICES = [
    ("Dovolenka", "Dovolenka"),
    ("PN", "PN"),
    ("OČR", "OČR"),
    ("Náhradné voľno", "Náhradné voľno"),
]


class AbsenceForm(FlaskForm):
    employee_id = SelectField(
        "Zamestnanec",
        validators=[DataRequired(message="Vyber zamestnanca.")],
        coerce=int,
    )
    absence_type = SelectField(
        "Typ neprítomnosti",
        choices=ABSENCE_TYPE_CHOICES,
        validators=[DataRequired(message="Vyber typ neprítomnosti.")],
    )
    start_date = DateField(
        "Od",
        validators=[DataRequired(message="Zadaj dátum začiatku.")],
    )
    end_date = DateField(
        "Do",
        validators=[DataRequired(message="Zadaj dátum konca.")],
    )
    note = StringField(
        "Poznámka",
        validators=[Optional(), Length(max=255)],
    )


class LeaveRequestForm(FlaskForm):
    """Žiadosť zamestnanca o voľno (``/my-requests``)."""

    absence_type = SelectField(
        "Druh voľna",
        choices=[(name, name) for name in LEAVE_REQUEST_TYPES],
        validators=[DataRequired(message="Vyber druh voľna.")],
    )
    start_date = DateField(
        "Od",
        validators=[DataRequired(message="Zadaj dátum začiatku.")],
    )
    end_date = DateField(
        "Do",
        validators=[DataRequired(message="Zadaj dátum konca.")],
    )
    note = StringField(
        "Poznámka pre manažéra",
        validators=[Optional(), Length(max=255)],
    )


class LoginForm(FlaskForm):
    username = StringField(
        "Používateľské meno",
        validators=[DataRequired(message="Zadaj používateľské meno.")],
    )
    password = PasswordField(
        "Heslo",
        validators=[DataRequired(message="Zadaj heslo.")],
    )


class CredentialsForm(FlaskForm):
    """Vytvorenie/úprava prihlasovacích údajov zamestnanca (na
    stránke úpravy zamestnanca)."""

    username = StringField(
        "Používateľské meno",
        validators=[
            DataRequired(message="Zadaj používateľské meno."),
            Length(max=80),
        ],
    )
    password = PasswordField(
        "Heslo",
        validators=[Optional(), Length(min=6, message="Aspoň 6 znakov.")],
    )


USER_ROLE_CHOICES = [
    ("manager", "Manažér (vytvára smeny na pridelených pobočkách)"),
    ("admin", "Administrátor (plný prístup)"),
]


class StaffUserForm(FlaskForm):
    """Vytvorenie/úprava účtu admina alebo manažéra (stránka Účty).

    Pri úprave je heslo nepovinné (prázdne = nemení sa) a rola sa
    nemení. Zoznam pobočiek (``branch_ids``) sa doplňuje v route.
    """

    username = StringField(
        "Používateľské meno",
        validators=[
            DataRequired(message="Zadaj používateľské meno."),
            Length(max=80),
        ],
    )
    password = PasswordField(
        "Heslo",
        validators=[Optional(), Length(min=6, message="Aspoň 6 znakov.")],
    )
    role = SelectField(
        "Rola",
        choices=USER_ROLE_CHOICES,
        validators=[DataRequired(message="Vyber rolu.")],
    )
    branch_ids = SelectMultipleField(
        "Pobočky",
        coerce=int,
        choices=[],
        validators=[Optional()],
    )


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Aktuálne heslo",
        validators=[DataRequired(message="Zadaj aktuálne heslo.")],
    )
    new_password = PasswordField(
        "Nové heslo",
        validators=[
            DataRequired(message="Zadaj nové heslo."),
            Length(min=6, message="Aspoň 6 znakov."),
        ],
    )
    confirm_password = PasswordField(
        "Nové heslo znova",
        validators=[
            DataRequired(message="Zopakuj nové heslo."),
            EqualTo("new_password", message="Heslá sa nezhodujú."),
        ],
    )

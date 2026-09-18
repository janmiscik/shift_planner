"""Flask-WTF formuláre - CSRF ochrana a backendová validácia vstupov."""

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    FloatField,
    SelectField,
    StringField,
    TimeField,
)
from wtforms.validators import (
    DataRequired,
    Length,
    NumberRange,
    Optional,
)


class EmployeeForm(FlaskForm):
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
    name = StringField(
        "Názov oddelenia",
        validators=[
            DataRequired(message="Zadaj názov oddelenia."),
            Length(max=100),
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

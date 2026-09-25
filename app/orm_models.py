"""SQLAlchemy modely.

Schéma je zámerne 1:1 kompatibilná s pôvodnými ručnými SQL
migráciami (rovnaké tabuľky, stĺpce aj typy), aby prechod na ORM
nevyžadoval žiadnu deštruktívnu zmenu existujúcich databáz.

Vzťah zamestnanec <-> oddelenie je M:N s "cez seba" tabuľkou
(``EmployeeDepartment``), pretože každé priradenie so sebou nesie aj
``weekly_hours`` (koľko hodín z fondu zamestnanca patrí danému
oddeleniu) - nie je to teda čisté M:N, ale asociačný objekt.
Vďaka ``association_proxy`` sa dá napriek tomu pristupovať jednoducho:
``employee.departments`` vráti rovno zoznam ``Department`` objektov.
"""

from flask_login import UserMixin
from sqlalchemy.ext.associationproxy import association_proxy

from app.extensions import db


class Employee(db.Model):
    __tablename__ = "employees"

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String, nullable=False)
    last_name = db.Column(db.String, nullable=False)
    position = db.Column(db.String, nullable=False)
    employment_type = db.Column(db.String, nullable=False)
    weekly_hours = db.Column(db.Float, nullable=True)
    active = db.Column(db.Integer, nullable=False, default=1)

    shifts = db.relationship(
        "Shift",
        back_populates="employee",
        cascade="all, delete-orphan",
        order_by="Shift.shift_date, Shift.start_time",
    )

    absences = db.relationship(
        "Absence",
        back_populates="employee",
        cascade="all, delete-orphan",
        order_by="Absence.start_date",
    )

    department_links = db.relationship(
        "EmployeeDepartment",
        back_populates="employee",
        cascade="all, delete-orphan",
    )

    # employee.departments -> zoznam Department objektov (bez nutnosti
    # ručne prechádzať department_links).
    departments = association_proxy("department_links", "department")

    user = db.relationship(
        "User",
        back_populates="employee",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Employee {self.id} {self.first_name} {self.last_name}>"


class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False, unique=True)
    active = db.Column(db.Integer, nullable=False, default=1)
    min_staff = db.Column(db.Integer, nullable=True)

    employee_links = db.relationship(
        "EmployeeDepartment",
        back_populates="department",
    )

    def __repr__(self):
        return f"<Department {self.id} {self.name}>"


class EmployeeDepartment(db.Model):
    """Priradenie zamestnanca k oddeleniu vrátane počtu hodín za
    týždeň, ktoré mu z jeho celkového fondu patria v danom oddelení.
    """

    __tablename__ = "employee_departments"
    __table_args__ = (
        db.UniqueConstraint(
            "employee_id", "department_id", name="uq_employee_department"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(
        db.Integer,
        db.ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    weekly_hours = db.Column(db.Float, nullable=False)

    employee = db.relationship("Employee", back_populates="department_links")
    department = db.relationship(
        "Department", back_populates="employee_links"
    )

    def __repr__(self):
        return (
            f"<EmployeeDepartment employee={self.employee_id} "
            f"department={self.department_id} {self.weekly_hours}h>"
        )


class Shift(db.Model):
    __tablename__ = "shifts"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(
        db.Integer,
        db.ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    shift_date = db.Column(db.String, nullable=False)
    start_time = db.Column(db.String, nullable=False)
    end_time = db.Column(db.String, nullable=False)
    shift_type = db.Column(db.String, nullable=False)
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # TEXT (nie DateTime) zámerne - staršie riadky (spred zavedenia
    # sledovania) majú namiesto dátumu text
    # "neznámy (pred zavedením sledovania)".
    created_at = db.Column(db.String, nullable=True)

    employee = db.relationship("Employee", back_populates="shifts")
    department = db.relationship("Department")

    def __repr__(self):
        return (
            f"<Shift {self.id} employee={self.employee_id} "
            f"{self.shift_date} {self.start_time}-{self.end_time}>"
        )


class Absence(db.Model):
    """Neprítomnosť zamestnanca (dovolenka, PN, OČR, náhradné voľno).

    ``start_date``/``end_date`` sú vrátane (celodenná neprítomnosť po
    jednotlivých dňoch - shodné s tým, ako appka pracuje s dátumami
    smien).
    """

    __tablename__ = "absences"

    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(
        db.Integer,
        db.ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
    )
    absence_type = db.Column(db.String, nullable=False)
    start_date = db.Column(db.String, nullable=False)
    end_date = db.Column(db.String, nullable=False)
    note = db.Column(db.String, nullable=True)
    created_at = db.Column(db.String, nullable=True)

    employee = db.relationship("Employee", back_populates="absences")

    def __repr__(self):
        return (
            f"<Absence {self.id} employee={self.employee_id} "
            f"{self.absence_type} {self.start_date}-{self.end_date}>"
        )


class User(UserMixin, db.Model):
    """Prihlasovací účet.

    ``role`` je buď "manager" (plný prístup) alebo "employee"
    (prístup len na čítanie vlastného rozpisu - viazaný na
    konkrétneho zamestnanca cez ``employee_id``).
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String, nullable=False, unique=True)
    password_hash = db.Column(db.String, nullable=False)
    role = db.Column(db.String, nullable=False)
    employee_id = db.Column(
        db.Integer,
        db.ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=True,
    )

    employee = db.relationship("Employee", back_populates="user")

    def __repr__(self):
        return f"<User {self.id} {self.username} ({self.role})>"

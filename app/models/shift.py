"""Model pracovnej smeny."""


class Shift:
    """Predstavuje jednu pracovnú smenu."""

    def __init__(
        self,
        employee,
        shift_date,
        start_time,
        end_time,
        shift_type,
    ):
        self.employee = employee
        self.shift_date = shift_date
        self.start_time = start_time
        self.end_time = end_time
        self.shift_type = shift_type

    def __str__(self):
        return (
            f"{self.employee.full_name} | "
            f"{self.shift_date} | "
            f"{self.shift_type} | "
            f"{self.start_time} - {self.end_time}"
        )
"""Model zamestnanca."""


class Employee:
    """Predstavuje jedného zamestnanca."""

    def __init__(
        self,
        first_name,
        last_name,
        position,
        employment_type="full-time",
    ):
        self.first_name = first_name
        self.last_name = last_name
        self.position = position
        self.employment_type = employment_type
        self.active = True

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    def __str__(self):
        return self.full_name
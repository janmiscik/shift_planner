"""Pomocné funkcie pre prácu s textom."""


def same_name(first, second):
    """Zhodujú sa dva názvy bez ohľadu na veľkosť písmen a okolité
    medzery?

    Používa ``casefold`` v Pythone namiesto SQL ``lower()``: SQLite
    pozná veľké/malé písmená len pre ASCII, takže "Štandard" a
    "štandard" by databáza považovala za rôzne názvy.
    """

    return (first or "").strip().casefold() == (second or "").strip().casefold()

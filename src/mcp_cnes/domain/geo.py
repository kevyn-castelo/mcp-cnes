"""Limites aproximados das UFs para qualificar coordenadas cadastrais."""

from __future__ import annotations

# (latitude mínima, latitude máxima, longitude mínima, longitude máxima).
# São caixas deliberadamente conservadoras, não polígonos administrativos.
UF_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "AC": (-11.2, -7.0, -74.0, -66.5),
    "AL": (-10.7, -8.7, -38.4, -35.0),
    "AP": (0.7, 4.6, -55.0, -49.7),
    "AM": (-9.9, 2.4, -74.0, -56.0),
    "BA": (-18.5, -8.4, -46.8, -37.2),
    "CE": (-8.0, -2.7, -41.6, -37.1),
    "DF": (-16.2, -15.4, -48.4, -47.2),
    "ES": (-21.4, -17.8, -42.0, -39.5),
    "GO": (-19.6, -12.3, -53.4, -45.8),
    "MA": (-10.4, -0.9, -48.1, -41.7),
    "MG": (-23.1, -14.1, -51.2, -39.7),
    "MS": (-24.3, -17.0, -58.3, -50.8),
    "MT": (-18.1, -7.2, -61.7, -50.1),
    "PA": (-10.0, 2.7, -59.0, -45.9),
    "PB": (-8.4, -5.9, -38.9, -34.6),
    "PE": (-9.6, -7.0, -41.5, -32.2),
    "PI": (-11.1, -2.6, -46.1, -40.2),
    "PR": (-26.9, -22.4, -54.8, -47.9),
    "RJ": (-23.5, -20.6, -45.0, -40.8),
    "RN": (-7.1, -4.7, -38.7, -34.8),
    "RO": (-13.8, -7.8, -66.1, -59.6),
    "RR": (0.8, 5.4, -65.0, -58.7),
    "RS": (-33.9, -26.9, -57.8, -49.6),
    "SC": (-29.5, -25.8, -54.0, -48.2),
    "SE": (-11.7, -9.4, -38.4, -36.2),
    "SP": (-25.5, -19.6, -53.3, -43.9),
    "TO": (-13.6, -5.0, -50.9, -45.6),
}


def coordinate_is_reliable(
    uf: str, latitude: float | None, longitude: float | None
) -> bool:
    """Informa se um ponto parseável cai na caixa aproximada da UF declarada."""

    bounds = UF_BOUNDS.get(uf.upper())
    return bool(
        bounds
        and latitude is not None
        and longitude is not None
        and bounds[0] <= latitude <= bounds[1]
        and bounds[2] <= longitude <= bounds[3]
    )

from shapely.geometry import Point


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return Point(a).distance(Point(b))

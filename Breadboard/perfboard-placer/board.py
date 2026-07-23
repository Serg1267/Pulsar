from dataclasses import dataclass


@dataclass
class Board:
    cols: int = 26
    rows: int = 31
    pitch_mm: float = 2.54
    board_width_mm: float = 70.0
    board_height_mm: float = 90.0

    def margin_x(self) -> float:
        return (self.board_width_mm - (self.cols - 1) * self.pitch_mm) / 2

    def margin_y(self) -> float:
        return (self.board_height_mm - (self.rows - 1) * self.pitch_mm) / 2

    def hole_pos(self, col: int, row: int) -> tuple[float, float]:
        return (
            self.margin_x() + col * self.pitch_mm,
            self.margin_y() + row * self.pitch_mm,
        )

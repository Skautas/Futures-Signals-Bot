class DirectionLock:
    def __init__(self):
        self.lock = None  # "BULLISH" | "BEARISH" | None

    def update(self, bos):
        if bos in ["BULLISH", "BEARISH"]:
            self.lock = bos

    def allows(self, side):
        if self.lock == "BULLISH" and side == "SHORT":
            return False
        if self.lock == "BEARISH" and side == "LONG":
            return False
        return True

def indicator_score(rsi, ema_alignment, direction):
    score = 0

    if direction == "LONG" and ema_alignment == "BULL":
        score += 1
    if direction == "SHORT" and ema_alignment == "BEAR":
        score += 1
    if 30 < rsi < 70:
        score += 1

    return score

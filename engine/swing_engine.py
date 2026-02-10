def process_signal(signal, direction_lock, log=print):
    side = signal.get("side") or signal.get("direction")
    if not direction_lock.allows(side):
        log(f"HTF LOCK BLOCKED: {side}")
        return None
    return signal

"""Keys and timing constants shared by walk interfaces."""

KEY_SNAPSHOT = "s"
KEY_BENCHMARK = "b"
KEY_SWITCH = "l"
KEY_NEW = "n"
KEY_FLOOR = "f"
KEY_COMPARE = "c"
KEY_SPEED_PROBE = "t"
KEY_QUIT = "q"
KEY_QUIT_UPPER = "Q"
QUIT_WORDS = ("q", "quit", "exit")

KEY_CTRL_C = 3
KEY_NO_INPUT = -1

POLL_TIMEOUT_MIN_MS = 50
FALLBACK_SETTLE_SLEEP = 0.1
DB_OPEN_FAIL_SLEEP = 2.0
WORKER_DRAIN_TIMEOUT = 130.0


def poll_timeout_ms(interval: float) -> int:
    """Poll getch timeout for walk loop; floor keeps fast intervals usable."""
    return max(POLL_TIMEOUT_MIN_MS, int(interval * 1000))

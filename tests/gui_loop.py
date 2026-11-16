"""Run a window's event loop in a test until a condition holds (or a deadline passes).

quit() is repeated every 50 ms until mainloop() really returns: CustomTkinter's first mainloop() runs update()
before Tkinter's own loop, and a quit() that runs inside that step is lost, because Tkinter clears its quit
flag when the real loop starts. A test that called quit() only once could then run forever."""
import time


def run_until(app, done, timeout: float = 30.0, first_check_ms: int = 200) -> bool:
    """Run app.mainloop() until done() is true or `timeout` seconds pass. Returns done() at the end."""
    deadline = time.monotonic() + timeout

    def check():
        if done() or time.monotonic() > deadline:
            app.quit()
        app.after(50, check)          # keep asking: a quit() made before the real loop started is lost

    app.after(first_check_ms, check)
    app.mainloop()
    return bool(done())

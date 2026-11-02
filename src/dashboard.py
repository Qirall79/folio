from trulens.core import TruSession
from trulens.dashboard import run_dashboard, stop_dashboard


def main():
    session = TruSession()
    run_dashboard(session, port=8501, address="127.0.0.1")

    try:
        input("Dashboard: http://127.0.0.1:8501 — press Enter to stop.\n")
    finally:
        stop_dashboard(session)


if __name__ == "__main__":
    main()
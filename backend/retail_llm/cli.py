"""Ask questions from the terminal:

    python -m retail_llm.cli "what was total revenue today?"
    python -m retail_llm.cli          # interactive REPL
"""
import sys

try:  # Windows consoles default to cp1252 and choke on non-latin chars
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from .pipeline import QueryError, answer


def _run(question: str):
    try:
        res = answer(question)
    except QueryError as e:
        print(f"  ! {e}")
        return
    print(f"\n  {res['answer']}\n")
    print(f"  [{res['source']}] {res['row_count']} rows")
    print(f"  {res['sql']}\n")


def main():
    if len(sys.argv) > 1:
        _run(" ".join(sys.argv[1:]))
        return
    print("Retail LLM query demo — Ctrl-C to quit")
    try:
        while True:
            q = input("\n> ").strip()
            if q:
                _run(q)
    except (EOFError, KeyboardInterrupt):
        print()


if __name__ == "__main__":
    main()

"""Terminal entry for OrionHarness."""

from harness.planner import coordinate


def main() -> None:
    print("OrionHarness — type a query, or 'quit' to exit.")
    while True:
        query = input("[orion] ").strip()
        if query.lower() in {"quit", "exit"}:
            break
        if not query:
            continue
        try:
            result = coordinate(query, kb_enabled=True)
            print(f"\n{result.final_response}\n")
        except Exception as exc:
            print(f"Error: {exc}")


if __name__ == "__main__":
    main()

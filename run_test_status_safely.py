
import subprocess
import sys

def run():
    try:
        result = subprocess.run(
            [sys.executable, "test_order_status.py"],
            capture_output=True,
            text=True
        )
        print("STDOUT:", result.stdout[:500])
        print("STDERR START")
        print(result.stderr[:1000]) # Print first 1000 chars of error
        print("STDERR END")
    except Exception as e:
        print(f"Wrapper failed: {e}")

if __name__ == "__main__":
    run()

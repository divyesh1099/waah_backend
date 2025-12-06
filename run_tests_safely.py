
import subprocess
import sys

def run_tests():
    # Run pytest and capture byte output directly to avoid encoding issues in shell redirection
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "test_full_flow_e2e.py"],
        capture_output=True
    )
    
    # Decode manually, replacing errors
    stdout = result.stdout.decode('utf-8', errors='replace')
    stderr = result.stderr.decode('utf-8', errors='replace')
    
    print("STDOUT:", stdout)
    print("STDERR:", stderr)

if __name__ == "__main__":
    run_tests()

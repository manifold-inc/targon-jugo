import os
from jugo import sendErrorToEndon

def testEndon():
    # Make sure ENDON_URL is set
    if not os.getenv("ENDON_URL"):
        raise EnvironmentError("ENDON_URL environment variable must be set")

    # Test cases with different types of errors
    test_cases = [
        {
            "error": ValueError("Test integration error"),
            "endpoint": "integration_test/value_error"
        },
        {
            "error": KeyError("Missing required key"),
            "endpoint": "integration_test/key_error"
        },
        {
            "error": RuntimeError("Something went wrong"),
            "endpoint": "integration_test/runtime_error"
        }
    ]

    for test in test_cases:
        try:
            raise test["error"]
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            
            print(f"\nTesting error reporting for: {type(e).__name__}")
            print("-" * 50)
            
            # Send to Endon
            sendErrorToEndon(e, error_traceback, test["endpoint"])
            
if __name__ == "__main__":
    testEndon()

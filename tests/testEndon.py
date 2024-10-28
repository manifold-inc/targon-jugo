import os
from jugo import sendErrorToEndon

def testEndon():
    # Make sure ENDON_URL is set
    if not os.getenv("ENDON_URL"):
        raise EnvironmentError("ENDON_URL environment variable must be set")

    try:
        raise ValueError("Test integration error")
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        
        # Send to Endon
        sendErrorToEndon(e, error_traceback, "integration_test")

        print("Test error sent to Endon. Please check the Endon logs.")

#  call the function
if __name__ == "__main__":
    testEndon()

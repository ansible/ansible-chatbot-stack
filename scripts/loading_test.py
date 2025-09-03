import json
from locust import task, constant, FastHttpUser
import time

PROVIDER = "my_rhoai_dev"
MODEL_ID = "granite-3.3-8b-instruct"

prompt = "what is AAP ?"

query_data = dict(
    query=prompt,
    model=MODEL_ID,
    provider=PROVIDER,
)

expected_text = "Ansible Automation Platform"

headers = {"Content-Type": "application/json"}


def get_response_chuncks_text_from_stream(response):
    chuncks_text = ""
    stream = getattr(response, "stream", None)
    if not stream:
        raise ValueError("response has no stream attribute")

    chuncks_bytes = bytearray()
    while True:
        try:
            chunck = stream.next()
            chuncks_bytes += chunck
            # wait for data to be ready before reading next
            time.sleep(5)
        except StopIteration:
            stream.release()
            break

    chuncks_string = chuncks_bytes.decode("utf-8")
    for http_chunk in chuncks_string.split("\n"):
        if http_chunk:
            if http_chunk.startswith("data: "):
                chuck_data = json.loads(http_chunk.strip("data: "))
                if chuck_data.get("event", "") == "token":
                    token = chuck_data.get("data", {}).get("token", "")
                    chuncks_text += token

    return chuncks_text


class ChatTesting(FastHttpUser):
    wait_time = constant(30)

    @task
    def chat(self):
        with self.client.post(
            "/v1/streaming_query",
            data=json.dumps(query_data),
            headers=headers,
            stream=True,
            catch_response=True,
        ) as response:
            response.raise_for_status()
            text = get_response_chuncks_text_from_stream(response)
            if expected_text not in text:
                response.failure(
                    f"expected '{expected_text}' not in response text '{text}'"
                )
            else:
                response.success()


# command line
# uv run locust -f scripts/loading_test.py -t 120  --headless --users 10 --spawn-rate 10 -H http://localhost:8321

# web
# uv run locust -f scripts/loading_test.py -t 120 --users 10 --spawn-rate 10 -H http://localhost:8321

```shell
curl localhost:8080/ReimbursementAgentA2AServer/process_request \
    --json '{
  "jsonrpc": "2.0",
  "id": 1424643,
  "method": "SendMessage",
  "params": {
    "message": {
      "role": "ROLE_USER",
      "parts": [
        {
          "text": "Reimburse my hotel for my business trip of 5 nights for 1200USD"
        }
      ],
      "taskId": "0d9g9ea6-dcc67-43ee-a389-8s9g0e6es5554",
      "contextId": "5e00b4a6-dcc67-43ee-a389-0e2a65958444",
      "messageId": "9224947643702-7674c-417b-a0b0-f0741243c589"
    }
  }
}' | jq .
```

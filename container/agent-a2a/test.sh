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
          "text": "Reimburse my hotel for my business trip of 1 night for 99 USD . Date is 05/04/2025"
        }
      ],
      "taskId": "0d9g9ea6-dcc67-43ee-a389-8s9g0e6es5555",
      "contextId": "5e00b4a6-dcc67-43ee-a389-0e2a65958445",
      "messageId": "9224947643702-7674c-417b-a0b0-f0741243c588"
    }
  }
}' | jq .
```
--> missing info + add date
```shell
curl localhost:8080/ReimbursementAgentA2AServer/process_request \
    --json '{
  "jsonrpc": "2.0",
  "id": 1423,
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [
        {
          "kind": "text",
          "text": "The date of the transaction is 05/04/2025"
        }
      ],
      "taskId": "0d9g9ea6-dcc67-43ee-a389-8s9g0e6es5554",
      "contextId": "5e00b4a6-dcc67-43ee-a389-0e2a65958444",
      "messageId": "92249e73702-767c-417b-a06b0-5sg6g881243c589"
    },
    "metadata": {}
  }
}' | jq .
```          
# Optional: test the chat page JavaScript (needs Node.js)
Runs the real `index.html` and `app.js` inside jsdom with a scripted server and checks what a customer would see
(messages appear as plain text, ticket numbers, errors, keyboard, login/logout, one request at a time).

    cd frontend_tests
    npm install jsdom
    node frontend_test.js

Expected last line: `all 30 checks passed`. The Python test suites do not need Node.

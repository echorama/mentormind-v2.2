// Helper functions
function sanitizeHTML(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function formatResponse(text) {
    let sanitizedText = sanitizeHTML(text);
    sanitizedText = sanitizedText.replace(/\n/g, "<br>"); // Replace newline characters with line breaks
    return sanitizedText;
}

// Event listener
document.getElementById("send-btn").addEventListener("click", async () => {
    const userInput = document.getElementById("user-input").value;
    if (!userInput) return;

    const chatBox = document.getElementById("chat-box");

    // Add user's message
    chatBox.innerHTML += `<div class="user-message">${sanitizeHTML(userInput)}</div>`;

    try {
        // Fetch bot response
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: userInput }),
        });
        const data = await response.json();

        // Add bot's response
        if (data.response) {
            chatBox.innerHTML += `<div class="bot-message">${formatResponse(data.response)}</div>`;
        } else {
            chatBox.innerHTML += `<div class="bot-message error">Error: ${sanitizeHTML(data.error)}</div>`;
        }
    } catch (error) {
        chatBox.innerHTML += `<div class="bot-message error">Error: ${sanitizeHTML(error.message)}</div>`;
    }

    // Clear input and scroll
    document.getElementById("user-input").value = "";
    chatBox.scrollTo({ top: chatBox.scrollHeight, behavior: "smooth" });
});

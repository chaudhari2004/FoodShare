
(() => {
    "use strict";

    const root = document.getElementById("foodshare-chat");
    if (!root) return;

    const conversationId = Number(root.dataset.conversationId);
    const currentUserId = Number(root.dataset.currentUserId);

    const list = document.getElementById("chat-message-list");
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-input");
    const sendButton = document.getElementById("chat-send-button");
    const errorBox = document.getElementById("chat-error");

    const imageInput = document.getElementById("chat-image-input");
    const imageButton = document.getElementById("chat-image-button");
    const imagePreview = document.getElementById("chat-image-preview");
    const previewImage = document.getElementById("chat-image-preview-img");
    const imageFileName = document.getElementById("chat-image-file-name");
    const removeImageButton = document.getElementById("chat-remove-image");

    const MAX_IMAGE_SIZE = 5 * 1024 * 1024;
    const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp"];

    if (
        !list || !form || !input || !sendButton ||
        !errorBox || !imageInput || !imageButton ||
        !imagePreview || !previewImage ||
        !imageFileName || !removeImageButton
    ) {
        console.error("FoodShare chat HTML is missing required elements.");
        return;
    }

    if (typeof window.io !== "function") {
        showError("Chat library could not load. Check your internet connection.");
        sendButton.disabled = true;
        return;
    }

    const socket = window.io({ transports: ["websocket", "polling"] });

    let joined = false;
    let selectedImage = null;
    let previewUrl = null;
    let sending = false;

    function showError(message) {
        errorBox.textContent = message;
        errorBox.hidden = false;
    }

    function clearError() {
        errorBox.textContent = "";
        errorBox.hidden = true;
    }

    function scrollToLatest() {
        list.scrollTop = list.scrollHeight;
    }

    function clearSelectedImage() {
        selectedImage = null;
        imageInput.value = "";
        imagePreview.hidden = true;
        previewImage.removeAttribute("src");
        imageFileName.textContent = "";

        if (previewUrl) {
            URL.revokeObjectURL(previewUrl);
            previewUrl = null;
        }
    }

    function renderMessage(message) {
        if (!message ||
            Number(message.conversation_id) !== conversationId) {
            return;
        }

        if (
            message.message_id &&
            list.querySelector(
                `[data-message-id="${Number(message.message_id)}"]`
            )
        ) {
            return;
        }

        const firstMessage = document.getElementById("chat-first-message");
        if (firstMessage) firstMessage.remove();

        const article = document.createElement("article");
        article.className =
            `chat-message ${
                Number(message.sender_id) === currentUserId
                    ? "message-own"
                    : "message-other"
            }`;

        if (message.message_id) {
            article.dataset.messageId = String(Number(message.message_id));
        }

        if (Number(message.sender_id) !== currentUserId) {
            const sender = document.createElement("span");
            sender.className = "message-sender";
            sender.textContent = message.sender_name || "FoodShare user";
            article.appendChild(sender);
        }

        if (message.message) {
            const bubble = document.createElement("p");
            bubble.className = "message-bubble";
            bubble.textContent = message.message;
            article.appendChild(bubble);
        }

        if (message.image_url) {
            const link = document.createElement("a");
            link.className = "chat-image-link";
            link.href = message.image_url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";

            const image = document.createElement("img");
            image.className = "chat-message-image";
            image.src = message.image_url;
            image.alt = "Image shared in chat";
            image.loading = "lazy";

            link.appendChild(image);
            article.appendChild(link);
        }

        const time = document.createElement("time");
        time.className = "message-time";
        time.textContent = message.created_at || "Just now";
        article.appendChild(time);

        list.appendChild(article);
        scrollToLatest();
    }

    socket.on("connect", () => {
        clearError();
        socket.emit("join_chat", {
            conversation_id: conversationId
        });
    });

    socket.on("chat_joined", (data) => {
        if (Number(data.conversation_id) === conversationId) {
            joined = true;
            sendButton.disabled = false;
            imageButton.disabled = false;
        }
    });

    socket.on("new_message", renderMessage);

    socket.on("chat_error", (data) => {
        showError(
            data && data.message
                ? data.message
                : "Chat error. Please try again."
        );
    });

    socket.on("disconnect", () => {
        joined = false;
        sendButton.disabled = true;
        imageButton.disabled = true;
        showError("Connection lost. Reconnecting…");
    });

    socket.on("connect_error", () => {
        showError("Could not connect to chat. Retrying…");
    });

    imageButton.addEventListener("click", () => {
        if (!joined || sending) return;
        imageInput.click();
    });

    imageInput.addEventListener("change", () => {
        clearError();

        const file = imageInput.files && imageInput.files[0];
        if (!file) return;

        if (!ALLOWED_TYPES.includes(file.type)) {
            clearSelectedImage();
            showError("Choose a JPG, PNG, or WebP image.");
            return;
        }

        if (file.size > MAX_IMAGE_SIZE) {
            clearSelectedImage();
            showError("Image must be 5 MB or smaller.");
            return;
        }

        if (previewUrl) URL.revokeObjectURL(previewUrl);

        selectedImage = file;
        previewUrl = URL.createObjectURL(file);

        previewImage.src = previewUrl;
        imageFileName.textContent = file.name;
        imagePreview.hidden = false;
    });

    removeImageButton.addEventListener("click", () => {
        clearSelectedImage();
        clearError();
    });

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        clearError();

        const message = input.value.trim();

        if (!message && !selectedImage) {
            showError("Write a message or attach an image.");
            return;
        }

        if (message.length > 2000) {
            showError("Message must be 2000 characters or fewer.");
            return;
        }

        if (!joined || !socket.connected) {
            showError("Chat is reconnecting. Please wait.");
            return;
        }

        if (sending) return;

        sending = true;
        sendButton.disabled = true;
        imageButton.disabled = true;

        try {
            if (selectedImage) {
                const formData = new FormData();
                formData.append("image", selectedImage);
                formData.append("message", message);
                formData.append("conversation_id", String(conversationId));

                /*
                 * Flask endpoint to be implemented next:
                 * POST /chat/<conversation_id>/upload-image
                 *
                 * Expected JSON response:
                 * { "success": true, "message": { ... } }
                 *
                 * The server must authorize the user, save the image,
                 * store its filename, and broadcast "new_message".
                 */
                const response = await fetch(
                    `/chat/${conversationId}/upload-image`,
                    {
                        method: "POST",
                        body: formData,
                        credentials: "same-origin"
                    }
                );

                const result = await response.json().catch(() => ({}));

                if (!response.ok || !result.success) {
                    throw new Error(
                        result.message || "Image upload failed."
                    );
                }

                if (result.message) {
                    renderMessage(result.message);
                }

                clearSelectedImage();
                input.value = "";
                input.style.height = "auto";
            } else {
                socket.emit("send_message", {
                    conversation_id: conversationId,
                    message
                });

                input.value = "";
                input.style.height = "auto";
            }
        } catch (error) {
            showError(error.message || "Could not send. Please try again.");
        } finally {
            sending = false;
            sendButton.disabled = !joined || !socket.connected;
            imageButton.disabled = !joined || !socket.connected;
        }
    });

    input.addEventListener("input", () => {
        input.style.height = "auto";
        input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
    });

    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    sendButton.disabled = true;
    imageButton.disabled = true;
    scrollToLatest();
})();


document.addEventListener("DOMContentLoaded", () => {
    const toggle = document.getElementById("notificationToggle");
    const dropdown = document.getElementById("notificationDropdown");
    const countBadge = document.getElementById("notificationCount");
    const itemsContainer = document.getElementById("notificationItems");
    const markAllButton = document.getElementById("notificationMarkAll");
    const wrapper = document.getElementById("notificationWrapper");

    if (!toggle || !dropdown || !countBadge || !itemsContainer) {
        console.warn("FoodShare notification elements were not found.");
        return;
    }

    let notifications = [];
    let socket = null;
    let loading = false;

    function escapeHTML(value) {
        return String(value ?? "").replace(/[&<>"']/g, character => ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;"
        })[character]);
    }

    function updateCount(count) {
        const unread = Math.max(0, Number(count) || 0);

        countBadge.textContent = unread > 99 ? "99+" : String(unread);
        countBadge.classList.toggle("visible", unread > 0);
        countBadge.setAttribute(
            "aria-label",
            `${unread} unread notifications`
        );
    }

    function renderNotifications() {
        if (!notifications.length) {
            itemsContainer.innerHTML = `
                <div class="notification-empty">
                    <div style="font-size: 28px; margin-bottom: 8px;">🔔</div>
                    <strong>You're all caught up!</strong>
                    <p style="margin: 6px 0 0;">
                        New food requests and updates will appear here.
                    </p>
                </div>
            `;
            return;
        }

        itemsContainer.innerHTML = notifications.map(item => {
            const id = Number(item.notification_id);
            const unreadClass = item.is_read ? "" : " unread";

            // Only allow internal FoodShare links.
            const rawUrl = typeof item.related_url === "string"
                ? item.related_url
                : "";

            const safeUrl = rawUrl.startsWith("/") &&
                !rawUrl.startsWith("//") &&
                !rawUrl.startsWith("/\\")
                ? rawUrl
                : "";

            const title = escapeHTML(item.title);
            const message = escapeHTML(item.message);
            const date = escapeHTML(item.created_at || "");

            return `
                <a href="${escapeHTML(safeUrl || "#")}"
                   class="notification-item${unreadClass}"
                   data-notification-id="${id}"
                   ${item.is_read ? "" : 'aria-label="Unread notification"'}>
                    <strong>${title}</strong>
                    <p>${message}</p>
                    <time>${date}</time>
                </a>
            `;
        }).join("");
    }

    async function loadNotifications() {
        if (loading) return;

        loading = true;

        try {
            const response = await fetch("/api/notifications", {
                method: "GET",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json"
                }
            });

            if (!response.ok) {
                throw new Error(`Notification API returned ${response.status}`);
            }

            const data = await response.json();

            if (!data.success) {
                throw new Error(data.message || "Could not load notifications.");
            }

            notifications = Array.isArray(data.notifications)
                ? data.notifications
                : [];

            updateCount(data.unread_count);
            renderNotifications();
        } catch (error) {
            console.error("FoodShare notification loading failed:", error);

            itemsContainer.innerHTML = `
                <div class="notification-empty">
                    Unable to load notifications.
                    <br>
                    Please refresh the page and try again.
                </div>
            `;
        } finally {
            loading = false;
        }
    }

    async function markNotificationRead(notificationId) {
        const item = notifications.find(
            notification =>
                Number(notification.notification_id) === notificationId
        );

        if (!item || item.is_read) return true;

        try {
            const response = await fetch(
                `/api/notifications/${notificationId}/read`,
                {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Accept": "application/json"
                    }
                }
            );

            const data = await response.json();

            if (!response.ok || !data.success) {
                throw new Error(data.message || "Could not mark as read.");
            }

            item.is_read = true;

            updateCount(
                notifications.filter(notification => !notification.is_read).length
            );

            renderNotifications();
            return true;
        } catch (error) {
            console.error("FoodShare mark-as-read failed:", error);
            return false;
        }
    }

    async function markAllNotificationsRead() {
        if (!markAllButton) return;

        markAllButton.disabled = true;

        try {
            const response = await fetch("/api/notifications/read-all", {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json"
                }
            });

            const data = await response.json();

            if (!response.ok || !data.success) {
                throw new Error(data.message || "Could not mark all as read.");
            }

            notifications.forEach(item => {
                item.is_read = true;
            });

            updateCount(0);
            renderNotifications();
        } catch (error) {
            console.error("FoodShare mark-all-read failed:", error);
        } finally {
            markAllButton.disabled = false;
        }
    }

    // Open and close the notification dropdown.
    toggle.addEventListener("click", async () => {
        const isOpening = dropdown.hidden;

        dropdown.hidden = !isOpening;
        toggle.setAttribute("aria-expanded", String(isOpening));

        if (isOpening) {
            await loadNotifications();
        }
    });

    // Close when clicking outside the dropdown.
    document.addEventListener("click", event => {
        if (wrapper && !wrapper.contains(event.target)) {
            dropdown.hidden = true;
            toggle.setAttribute("aria-expanded", "false");
        }
    });

    // Close with Escape.
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && !dropdown.hidden) {
            dropdown.hidden = true;
            toggle.setAttribute("aria-expanded", "false");
            toggle.focus();
        }
    });

    // Mark an individual notification as read.
    itemsContainer.addEventListener("click", async event => {
        const link = event.target.closest("[data-notification-id]");

        if (!link) return;

        const notificationId = Number(link.dataset.notificationId);

        if (!Number.isInteger(notificationId) || notificationId <= 0) {
            event.preventDefault();
            return;
        }

        const item = notifications.find(
            notification =>
                Number(notification.notification_id) === notificationId
        );

        if (item && !item.is_read) {
            // Wait for the read request before navigating away.
            event.preventDefault();

            const marked = await markNotificationRead(notificationId);
            const destination = link.getAttribute("href");

            if (marked && destination && destination !== "#") {
                window.location.assign(destination);
            }
        } else if (!link.getAttribute("href") ||
                   link.getAttribute("href") === "#") {
            event.preventDefault();
        }
    });

    if (markAllButton) {
        markAllButton.addEventListener("click", markAllNotificationsRead);
    }

    // Real-time notifications from Flask-SocketIO.
    if (typeof window.io === "function") {
        socket = window.io();

        socket.on("connect", () => {
            console.log("FoodShare notification socket connected.");
            loadNotifications();
        });

        socket.on("new_notification", notification => {
            if (!notification ||
                !Number.isInteger(Number(notification.notification_id))) {
                loadNotifications();
                return;
            }

            const id = Number(notification.notification_id);

            const existingIndex = notifications.findIndex(
                item => Number(item.notification_id) === id
            );

            if (existingIndex !== -1) {
                notifications.splice(existingIndex, 1);
            }

            notifications.unshift({
                ...notification,
                notification_id: id,
                is_read: Boolean(notification.is_read)
            });

            notifications = notifications.slice(0, 20);

            updateCount(
                notifications.filter(item => !item.is_read).length
            );

            renderNotifications();
        });

        socket.on("connect_error", error => {
            console.error("Notification socket connection failed:", error);
        });
    }

    // Load notifications and unread count on page load.
    loadNotifications();
});

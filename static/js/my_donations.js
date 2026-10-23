
document.addEventListener("DOMContentLoaded", function () {
    const searchInput = document.getElementById("donationSearch");
    const statusFilter = document.getElementById("donationStatusFilter");
    const donationCards = document.querySelectorAll(".donation-card");
    const noResults = document.getElementById("donationNoResults");

    if (!searchInput || !statusFilter || !noResults) {
        return;
    }

    function filterDonations() {
        const searchTerm = searchInput.value.trim().toLowerCase();
        const selectedStatus = statusFilter.value;
        let visibleCount = 0;

        donationCards.forEach(function (card) {
            const foodName = card.dataset.name || "";
            const status = card.dataset.status || "";

            const matchesName = foodName.includes(searchTerm);
            const matchesStatus =
                selectedStatus === "all" || status === selectedStatus;

            const shouldShow = matchesName && matchesStatus;

            card.hidden = !shouldShow;

            if (shouldShow) {
                visibleCount++;
            }
        });

        noResults.hidden = visibleCount !== 0;
    }

    searchInput.addEventListener("input", filterDonations);
    statusFilter.addEventListener("change", filterDonations);
});

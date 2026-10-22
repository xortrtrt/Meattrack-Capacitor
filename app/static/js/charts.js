(function () {
    function formatMoney(value) {
        return `PHP ${Number(value || 0).toLocaleString("en-US", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        })}`;
    }

    function formatQuantity(value) {
        const quantity = Number(value || 0);
        return quantity.toLocaleString("en-US", {
            minimumFractionDigits: Number.isInteger(quantity) ? 0 : 2,
            maximumFractionDigits: Number.isInteger(quantity) ? 0 : 2,
        });
    }
    function bindInventoryMovementChart() {
        const canvas = document.querySelector("[data-inventory-movement-chart]");
        const payload = document.querySelector("[data-inventory-movement-json]");
        if (!canvas || !payload) {
            return;
        }

        let rows = [];
        try {
            rows = JSON.parse(payload.textContent || "[]");
        } catch (error) {
            rows = [];
        }

        if (!rows.length || !window.Chart) {
            return;
        }

        const labels = rows.map((row) => row.name);
        const stockIn = rows.map((row) => Number(row.total_in || 0));
        const stockOut = rows.map((row) => Number(row.total_out || 0));
        const units = rows.map((row) => row.unit || "pack");

        new window.Chart(canvas.getContext("2d"), {
            type: "bar",
            data: {
                labels,
                datasets: [
                    {
                        label: "Stock in",
                        data: stockIn,
                        backgroundColor: "rgba(223, 190, 29, 0.82)",
                        borderColor: "#d9b90f",
                        borderWidth: 1,
                        borderRadius: 8,
                    },
                    {
                        label: "Stock out",
                        data: stockOut,
                        backgroundColor: "rgba(91, 48, 25, 0.82)",
                        borderColor: "#5b3019",
                        borderWidth: 1,
                        borderRadius: 8,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: "y",
                scales: {
                    x: {
                        beginAtZero: true,
                        ticks: {
                            precision: 0,
                            color: "#675b44",
                        },
                        grid: {
                            color: "rgba(215, 201, 154, 0.45)",
                        },
                    },
                    y: {
                        ticks: {
                            color: "#23150f",
                            font: {
                                weight: 700,
                            },
                        },
                        grid: {
                            display: false,
                        },
                    },
                },
                plugins: {
                    legend: {
                        labels: {
                            color: "#23150f",
                            boxWidth: 14,
                            boxHeight: 14,
                            useBorderRadius: true,
                        },
                    },
                    tooltip: {
                        callbacks: {
                            label(context) {
                                const unit = units[context.dataIndex] || "pack";
                                return `${context.dataset.label}: ${formatQuantity(context.parsed.x)} ${unit}`;
                            },
                        },
                    },
                },
            },
        });

        const fallback = document.querySelector("[data-inventory-movement-fallback]");
        if (fallback) {
            fallback.hidden = true;
        }
    }
    function bindOwnerSalesChart() {
        const canvas = document.querySelector("[data-owner-sales-chart]");
        const payload = document.querySelector("[data-owner-sales-json]");
        const form = document.querySelector("[data-owner-sales-form]");
        const periodSelect = form?.querySelector('select[name="sales_period"]');
        if (!canvas || !payload || !window.Chart) {
            return;
        }

        let rows = [];
        try {
            rows = JSON.parse(payload.textContent || "[]");
        } catch (error) {
            rows = [];
        }
        if (!rows.length) {
            return;
        }

        function buildOptions(sourceRows) {
            const maxSales = Math.max(...sourceRows.map((row) => Number(row.sales || 0)), 0);
            const suggestedMax = Math.max(100, Math.ceil(maxSales / 100) * 100);
            return {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        ticks: {
                            color: "#675b44",
                            maxRotation: 0,
                            autoSkip: true,
                        },
                        grid: {
                            display: false,
                        },
                    },
                    y: {
                        beginAtZero: true,
                        suggestedMax,
                        ticks: {
                            stepSize: 100,
                            color: "#675b44",
                            callback(value) {
                                return `PHP ${Number(value).toLocaleString("en-US")}`;
                            },
                        },
                        grid: {
                            color: "rgba(215, 201, 154, 0.45)",
                        },
                    },
                },
                plugins: {
                    legend: {
                        labels: {
                            color: "#23150f",
                            boxWidth: 14,
                            boxHeight: 14,
                        },
                    },
                    tooltip: {
                        callbacks: {
                            label(context) {
                                const row = sourceRows[context.dataIndex] || {};
                                return `${formatMoney(context.parsed.y)} · ${formatQuantity(row.orders)} orders`;
                            },
                        },
                    },
                },
            };
        }

        function renderChart(nextRows) {
            if (canvas.ownerSalesChart) {
                canvas.ownerSalesChart.destroy();
            }
            canvas.ownerSalesChart = new window.Chart(canvas.getContext("2d"), {
                type: "line",
                data: {
                    labels: nextRows.map((row) => row.label),
                    datasets: [
                        {
                            label: "Fulfilled sales",
                            data: nextRows.map((row) => Number(row.sales || 0)),
                            borderColor: "#d7b621",
                            backgroundColor: "rgba(215, 182, 33, 0.18)",
                            pointBackgroundColor: "#5b3019",
                            pointBorderColor: "#fff7dc",
                            pointRadius: 4,
                            pointHoverRadius: 6,
                            tension: 0.32,
                            fill: true,
                        },
                    ],
                },
                options: buildOptions(nextRows),
            });
        }

        renderChart(rows);

        const fallback = document.querySelector("[data-owner-sales-fallback]");
        if (fallback) {
            fallback.hidden = true;
        }

        if (!form || !periodSelect) {
            return;
        }

        async function refreshChart() {
            const endpoint = form.dataset.ownerSalesUrl || form.action || window.location.pathname;
            const url = new URL(endpoint, window.location.origin);
            const previousPeriod = new URLSearchParams(window.location.search).get("sales_period") || "daily";
            url.searchParams.set("sales_period", periodSelect.value);
            form.classList.add("is-loading");
            periodSelect.disabled = true;
            try {
                const response = await fetch(url, {
                    headers: {
                        Accept: "application/json",
                    },
                });
                if (!response.ok) {
                    throw new Error("Chart data could not be loaded.");
                }
                const data = await response.json();
                rows = Array.isArray(data.rows) ? data.rows : [];
                if (!rows.length) {
                    return;
                }
                payload.textContent = JSON.stringify(rows);
                renderChart(rows);
                const summary = document.querySelector("[data-owner-sales-summary]");
                if (summary) {
                    summary.querySelector("[data-owner-sales-total]").textContent = formatMoney(data.total);
                    summary.querySelector("[data-owner-sales-orders]").textContent = formatQuantity(data.orders);
                    summary.querySelector("[data-owner-sales-start]").textContent = data.range.start;
                    summary.querySelector("[data-owner-sales-end]").textContent = data.range.end;
                }
                const pageUrl = new URL(window.location.href);
                pageUrl.searchParams.set("sales_period", data.period || periodSelect.value);
                window.history.replaceState({}, "", pageUrl);
            } catch (error) {
                periodSelect.value = previousPeriod;
            } finally {
                periodSelect.disabled = false;
                form.classList.remove("is-loading");
            }
        }

        form.addEventListener("submit", (event) => {
            event.preventDefault();
            refreshChart();
        });
        periodSelect.addEventListener("change", refreshChart);
    }

    function bindTeamReportSales() {
        const form = document.querySelector("[data-team-report-form]");
        const data = document.getElementById("team-report-sales-data");
        if (!form || !data) {
            return;
        }

        let records = [];
        try {
            records = JSON.parse(data.textContent || "[]");
        } catch (_error) {
            records = [];
        }
        const startInput = form.querySelector('input[name="period_start"]');
        const endInput = form.querySelector('input[name="period_end"]');
        const salesInput = form.querySelector("[data-report-total-sales]");
        const ordersInput = form.querySelector("[data-report-total-orders]");
        const itemsContainer = document.querySelector("[data-report-order-items]");
        const orderCount = document.querySelector("[data-report-order-count]");
        const loadMoreButton = document.querySelector("[data-report-order-more]");
        const loadMoreLabel = loadMoreButton?.querySelector("[data-report-order-more-label]");
        if (!startInput || !endInput || !salesInput || !ordersInput) {
            return;
        }

        const businessDate = form.dataset.businessDate || "";
        startInput.value ||= businessDate;
        endInput.value ||= businessDate;

        const money = (value) => `PHP ${value.toLocaleString("en-PH", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        })}`;
        const quantity = (value) => Number(value || 0).toLocaleString("en-PH", {
            minimumFractionDigits: Number(value || 0) % 1 === 0 ? 0 : 2,
            maximumFractionDigits: 2,
        });
        const element = (tagName, className, text) => {
            const node = document.createElement(tagName);
            if (className) {
                node.className = className;
            }
            if (text !== undefined) {
                node.textContent = text;
            }
            return node;
        };

        const countLabel = (value, singular, plural = `${singular}s`) => (
            Number(value) === 1 ? singular : plural
        );
        const orderPageSize = 5;
        let visibleOrderLimit = orderPageSize;

        const updateVisibleOrders = () => {
            if (!itemsContainer || !loadMoreButton || !loadMoreLabel) {
                return;
            }
            const cards = Array.from(itemsContainer.querySelectorAll(".report-order-card"));
            cards.forEach((card, index) => {
                card.hidden = index >= visibleOrderLimit;
            });
            const visibleCount = Math.min(visibleOrderLimit, cards.length);
            const remaining = cards.length - visibleCount;
            loadMoreButton.hidden = remaining === 0;
            if (remaining > 0) {
                const nextCount = Math.min(orderPageSize, remaining);
                loadMoreLabel.textContent = `Show ${nextCount} more ${countLabel(nextCount, "order")}`;
                loadMoreButton.setAttribute(
                    "aria-label",
                    `Showing ${visibleCount} of ${cards.length} orders. Show ${nextCount} more.`,
                );
            }
        };

        const showEmptyItems = () => {
            if (!itemsContainer) {
                return;
            }
            visibleOrderLimit = orderPageSize;
            if (loadMoreButton) {
                loadMoreButton.hidden = true;
            }
            const empty = element("div", "report-order-empty");
            empty.innerHTML = '<i data-lucide="package-open" aria-hidden="true"></i><span>No fulfilled order items in this period.</span>';
            itemsContainer.replaceChildren(empty);
            window.lucide?.createIcons();
        };

        function updateTotals() {
            const start = startInput.value;
            const end = endInput.value;
            if (!start || !end || end < start) {
                salesInput.value = "PHP 0.00";
                ordersInput.value = "0 orders / 0 packs";
                if (orderCount) {
                    orderCount.textContent = "0 orders";
                }
                showEmptyItems();
                return;
            }

            const orderIds = new Set();
            const included = records.filter((record) => record.sale_date >= start && record.sale_date <= end);
            const sales = included.reduce((sum, record) => {
                orderIds.add(record.order_id);
                return sum + Number(record.total_sales || 0);
            }, 0);
            const packs = included.reduce((sum, record) => sum + Number(record.quantity || 0), 0);
            salesInput.value = money(sales);
            ordersInput.value = `${orderIds.size} ${countLabel(orderIds.size, "order")} / ${quantity(packs)} ${countLabel(packs, "pack")}`;
            if (orderCount) {
                orderCount.textContent = `${orderIds.size} ${countLabel(orderIds.size, "order")}`;
            }

            if (!itemsContainer) {
                return;
            }
            visibleOrderLimit = orderPageSize;
            itemsContainer.replaceChildren();
            if (included.length === 0) {
                showEmptyItems();
                return;
            }

            const groupedOrders = new Map();
            included.forEach((record) => {
                const orderKey = String(record.order_id);
                if (!groupedOrders.has(orderKey)) {
                    groupedOrders.set(orderKey, {
                        orderId: record.order_id,
                        customer: record.customer || "Retail counter",
                        items: [],
                        total: 0,
                    });
                }
                const order = groupedOrders.get(orderKey);
                order.items.push(record);
                order.total += Number(record.total_sales || 0);
            });

            groupedOrders.forEach((order) => {
                const card = element("details", "report-order-card");
                const summary = element("summary", "report-order-card-summary");

                const identity = element("div", "report-order-identity");
                identity.append(
                    element("span", "report-order-number", `Order #${order.orderId}`),
                    element("strong", "report-order-customer", order.customer),
                    element(
                        "small",
                        "report-order-product-count",
                        `${order.items.length} ${countLabel(order.items.length, "product")}`,
                    ),
                );

                const preview = element("div", "report-order-preview");
                preview.setAttribute(
                    "aria-label",
                    `${order.items.length} ${countLabel(order.items.length, "product")} in order ${order.orderId}`,
                );
                order.items.slice(0, 3).forEach((record) => {
                    const image = document.createElement("img");
                    image.src = record.product_image_url || "/static/img/hero-meat-counter.png";
                    image.alt = "";
                    image.loading = "lazy";
                    image.width = 44;
                    image.height = 44;
                    preview.appendChild(image);
                });
                if (order.items.length > 3) {
                    preview.appendChild(element("span", "report-order-preview-more", `+${order.items.length - 3}`));
                }

                const total = element("div", "report-order-total");
                total.append(element("small", "", "Order total"), element("strong", "", money(order.total)));

                const chevron = element("span", "report-order-chevron");
                chevron.setAttribute("aria-hidden", "true");
                chevron.innerHTML = '<i data-lucide="chevron-down"></i>';
                summary.append(identity, preview, total, chevron);

                const body = element("div", "report-order-card-body");
                const productList = element("div", "report-order-product-list");
                order.items.forEach((record) => {
                    const line = element("article", "report-order-product-line");
                    const image = document.createElement("img");
                    image.src = record.product_image_url || "/static/img/hero-meat-counter.png";
                    image.alt = record.product || "Product";
                    image.loading = "lazy";
                    image.width = 64;
                    image.height = 64;

                    const copy = element("div", "report-order-product-copy");
                    copy.append(
                        element("strong", "", record.product || "Product"),
                        element("small", "", `${quantity(record.quantity)} ${record.unit || "pack"}`),
                    );

                    const lineTotal = element("div", "report-order-line-total");
                    lineTotal.append(
                        element("small", "", "Sales"),
                        element("strong", "", money(Number(record.total_sales || 0))),
                    );
                    line.append(image, copy, lineTotal);
                    productList.appendChild(line);
                });
                body.appendChild(productList);
                card.append(summary, body);
                itemsContainer.appendChild(card);
            });
            updateVisibleOrders();
            window.lucide?.createIcons();
        }

        loadMoreButton?.addEventListener("click", () => {
            const firstHiddenCard = itemsContainer?.querySelector(".report-order-card[hidden]");
            visibleOrderLimit += orderPageSize;
            updateVisibleOrders();
            firstHiddenCard?.querySelector("summary")?.focus();
        });
        startInput.addEventListener("change", updateTotals);
        endInput.addEventListener("change", updateTotals);
        updateTotals();
    }

    bindInventoryMovementChart();
    bindOwnerSalesChart();
    bindTeamReportSales();
})();

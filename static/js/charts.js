// charts.js

// Function to render Courses Status chart
function renderCourseStatusChart(activeCourses, inactiveCourses) {
    const ctx = document.getElementById('courseStatusChart').getContext('2d');
    const courseStatusChart = new Chart(ctx, {
        type: 'doughnut', // or 'pie'
        data: {
            labels: ['Active Courses', 'Inactive Courses'],
            datasets: [{
                label: 'Courses Status',
                data: [activeCourses, inactiveCourses],
                backgroundColor: [
                    'rgba(40, 167, 69, 0.7)',  // Green for active
                    'rgba(220, 53, 69, 0.7)'   // Red for inactive
                ],
                borderColor: [
                    'rgba(40, 167, 69, 1)',
                    'rgba(220, 53, 69, 1)'
                ],
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { position: 'bottom' },
                tooltip: { enabled: true }
            }
        }
    });
}


// Function to render Enrollments per Course/Batch
function renderEnrollmentsChart(labels, data) {
    const ctx = document.getElementById('enrollmentsChart').getContext('2d');
    const enrollmentsChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,   // e.g., ['Python', 'Java', 'C++']
            datasets: [{
                label: 'Number of Enrollments',
                data: data,    // e.g., [12, 20, 8]
                backgroundColor: 'rgba(54, 162, 235, 0.7)',
                borderColor: 'rgba(54, 162, 235, 1)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        precision: 0   // whole numbers
                    },
                    title: {
                        display: true,
                        text: 'Enrollments'
                    }
                },
                x: {
                    title: {
                        display: true,
                        text: 'Courses / Batches'
                    }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: { enabled: true }
            }
        }
    });
}



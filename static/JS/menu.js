// Light/dark theme switch in the user menu, remembered in this browser
const themeButton = document.getElementById('theme-toggle');

function showTheme() {
    const dark = document.documentElement.dataset.theme === 'dark';
    if (themeButton) {
        themeButton.textContent = dark ? 'Light theme' : 'Dark theme';
    }
}

if (themeButton) {
    themeButton.addEventListener('click', () => {
        const dark = document.documentElement.dataset.theme !== 'dark';
        if (dark) {
            document.documentElement.dataset.theme = 'dark';
        } else {
            delete document.documentElement.dataset.theme;
        }
        try {
            localStorage.setItem('theme', dark ? 'dark' : 'light');
        } catch (e) {}
        showTheme();
    });
}
showTheme();

// Close the user menu when clicking anywhere else on the page
const userMenu = document.querySelector('.user-menu');
if (userMenu) {
    document.addEventListener('click', (event) => {
        if (!userMenu.contains(event.target)) {
            userMenu.open = false;
        }
    });
}

// "Copy link" buttons on the admin page
document.querySelectorAll('.copy-link').forEach((button) => {
    button.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(button.dataset.link);
            button.textContent = 'Copied!';
        } catch (e) {
            // Clipboard blocked: show the link so it can be copied by hand
            window.prompt('Copy this link:', button.dataset.link);
        }
        setTimeout(() => { button.textContent = 'Copy link'; }, 2000);
    });
});

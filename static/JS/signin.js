const password = document.getElementById('password');
const toggle = document.getElementById('toggle-password');

if (password && toggle) {
    toggle.addEventListener('click', () => {
        const showing = password.type === 'text';
        password.type = showing ? 'password' : 'text';
        toggle.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
    });
}
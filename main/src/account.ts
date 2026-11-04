import * as auth from './auth';
import * as toast from './toast';

// Header account controls and the sign-in / create-account dialog

export function initAccount() {
    const signInBtn = document.getElementById('sign-in-btn') as HTMLButtonElement;
    const signOutBtn = document.getElementById('sign-out-btn') as HTMLButtonElement;
    const emailLabel = document.getElementById('account-email') as HTMLSpanElement;
    const dialog = document.getElementById('auth-dialog') as HTMLDialogElement;
    const form = dialog.querySelector('form') as HTMLFormElement;
    const title = dialog.querySelector('.auth-title') as HTMLHeadingElement;
    const emailInput = form.elements.namedItem('email') as HTMLInputElement;
    const passwordInput = form.elements.namedItem('password') as HTMLInputElement;
    const passwordHint = dialog.querySelector('.auth-hint') as HTMLParagraphElement;
    const error = dialog.querySelector('.auth-error') as HTMLParagraphElement;
    const submit = dialog.querySelector('.auth-submit') as HTMLButtonElement;
    const switchText = dialog.querySelector('.auth-switch-text') as HTMLSpanElement;
    const switchBtn = dialog.querySelector('.auth-switch') as HTMLButtonElement;
    const cancel = dialog.querySelector('.auth-cancel') as HTMLButtonElement;

    let mode: 'sign-in' | 'register' = 'sign-in';

    const showError = (message: string | null) => {
        error.textContent = message ?? '';
        error.classList.toggle('hidden', !message);
    };

    const setMode = (next: typeof mode) => {
        mode = next;
        const registering = mode === 'register';
        title.textContent = registering ? 'Create an account' : 'Sign in';
        submit.textContent = registering ? 'Create account' : 'Sign in';
        switchText.textContent = registering ? 'Already have an account?' : 'New to Data DJ?';
        switchBtn.textContent = registering ? 'Sign in' : 'Create an account';
        passwordInput.autocomplete = registering ? 'new-password' : 'current-password';
        passwordInput.minLength = registering ? 8 : 0;
        passwordHint.classList.toggle('hidden', !registering);
        showError(null);
    };

    const showUser = (user: auth.User | null) => {
        signInBtn.classList.toggle('hidden', !!user);
        emailLabel.classList.toggle('hidden', !user);
        signOutBtn.classList.toggle('hidden', !user);
        emailLabel.textContent = user?.email ?? '';
        emailLabel.title = user ? `Signed in as ${user.email}. Favorites are saved to this account.` : '';
    };

    signInBtn.onclick = () => {
        setMode('sign-in');
        form.reset();
        dialog.showModal();
        emailInput.focus();
    };

    signOutBtn.onclick = () => {
        auth.logout();
        toast.newMessage('Signed out. Favorites are saved in this browser until you sign in again.');
    };

    switchBtn.onclick = () => {
        setMode(mode === 'sign-in' ? 'register' : 'sign-in');
        emailInput.focus();
    };
    cancel.onclick = () => dialog.close();

    form.onsubmit = async (event) => {
        event.preventDefault();
        showError(null);
        submit.disabled = true;
        try {
            const email = emailInput.value;
            const password = passwordInput.value;
            const user = mode === 'register' ? await auth.register(email, password) : await auth.login(email, password);
            dialog.close();
            toast.newMessage(mode === 'register' ? `Account created. Signed in as ${user.email}` : `Signed in as ${user.email}`);
        } catch (err) {
            showError(err instanceof auth.NetworkError
                ? "Can't reach the Data DJ server. Is the backend running?"
                : err instanceof Error ? err.message : 'Something went wrong.');
        } finally {
            submit.disabled = false;
        }
    };

    auth.onAuthChange(showUser);
    showUser(auth.getUser());
    auth.refreshUser();
}

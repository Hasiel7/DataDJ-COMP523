import * as toast from './toast';
import * as input from './inputManager';
import * as auth from './auth';
import { getViewState, applyViewState, type ViewState } from './header';
import { getRunState, applyRunState, describeRun, type RunState } from './runs';

// Saved views per file name: the result-switcher run (method and parameters)
// plus axes, color, filters and hidden legend entries. Signed out they live in
// this browser; signed in they live in the account, falling back to this
// browser when the server can't be reached.

const STORAGE_KEY = 'datadj.favorites.v1';
// Per user: whether we've already offered to copy this browser's favorites into the account
const OFFERED_KEY = 'datadj.favorites.copyOffered.';

interface Favorite {
    id: string;
    file: string;
    name: string;
    savedAt: number;
    run: RunState | null;
    view: ViewState;
}

type NewFavorite = Omit<Favorite, 'id' | 'savedAt'>;

interface FavoriteStore {
    list(file: string): Promise<Favorite[]>;
    save(favorite: NewFavorite): Promise<Favorite>;
    remove(id: string): Promise<void>;
}

// ---------- This browser ----------

function loadAll(): Favorite[] {
    try {
        const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]');
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [];
    }
}

function saveAll(favorites: Favorite[]) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(favorites));
}

const localStore: FavoriteStore = {
    async list(file) {
        return loadAll().filter((f) => f.file === file);
    },
    async save(favorite) {
        const saved: Favorite = { ...favorite, id: crypto.randomUUID(), savedAt: Date.now() };
        saveAll([...loadAll(), saved]);
        return saved;
    },
    async remove(id) {
        saveAll(loadAll().filter((f) => f.id !== id));
    },
};

// ---------- The account (API) ----------

interface ApiFavorite {
    id: string;
    file_name: string;
    name: string;
    payload: { run: RunState | null; view: ViewState };
    created_at: string;
}

function fromApi(favorite: ApiFavorite): Favorite {
    return {
        id: favorite.id,
        file: favorite.file_name,
        name: favorite.name,
        savedAt: Date.parse(favorite.created_at),
        run: favorite.payload.run ?? null,
        view: favorite.payload.view,
    };
}

const apiStore: FavoriteStore = {
    async list(file) {
        const favorites = await auth.apiRequest<ApiFavorite[]>(`/favorites?file=${encodeURIComponent(file)}`);
        return favorites.map(fromApi);
    },
    async save(favorite) {
        const saved = await auth.apiRequest<ApiFavorite>('/favorites', {
            method: 'POST',
            body: JSON.stringify({
                file_name: favorite.file,
                name: favorite.name,
                payload: { run: favorite.run, view: favorite.view },
            }),
        });
        return fromApi(saved);
    },
    async remove(id) {
        await auth.apiRequest<void>(`/favorites/${encodeURIComponent(id)}`, { method: 'DELETE' });
    },
};

// ---------- Choosing a store ----------

let fileName = '';
let lastApplied = -1; // for stepping through favorites on a knob
// The favorites the menu shows, so the knob can step through them without waiting on the server
let shown: Favorite[] = [];
// Which store `shown` came from, so deleting goes back to the same place
let shownFrom: FavoriteStore = localStore;
// Set while the server is unreachable, so the toast appears once rather than on every call
let offline = false;
let renderCount = 0;

const saveBtn = document.getElementById('favorite-save') as HTMLButtonElement;
const list = document.getElementById('favorites-list') as HTMLDivElement;
const copyDialog = document.getElementById('copy-favorites-dialog') as HTMLDialogElement;

// Run an operation on the account store, or on this browser's when signed out or unreachable
async function withStore<T>(op: (store: FavoriteStore) => Promise<T>): Promise<{ result: T; store: FavoriteStore }> {
    if (!auth.getUser()) return { result: await op(localStore), store: localStore };
    try {
        const result = await op(apiStore);
        offline = false;
        return { result, store: apiStore };
    } catch (error) {
        if (error instanceof auth.NetworkError) {
            if (!offline) toast.newError("Can't reach the server. Favorites are being kept in this browser for now.");
            offline = true;
            return { result: await op(localStore), store: localStore };
        }
        throw error;
    }
}

function reportError(error: unknown) {
    if (error instanceof auth.ApiError && error.status === 401) {
        toast.newError('Your session ended. Sign in again to use your account favorites.');
    } else {
        toast.newError(error instanceof Error ? error.message : 'Something went wrong with favorites.');
    }
}

function autoName(run: RunState | null, view: ViewState): string {
    const parts = [run ? describeRun(run) : `${view.x || '–'} × ${view.y || '–'}`];
    if (view.color) parts.push(`color ${view.color}`);
    const activeFilters = view.filters.filter((f) => f.value !== '').length;
    if (activeFilters) parts.push(`${activeFilters} filter${activeFilters > 1 ? 's' : ''}`);
    if (view.hiddenCategories.length) parts.push(`${view.hiddenCategories.length} hidden`);
    return parts.join(' · ');
}

async function saveCurrent() {
    if (!fileName) return;
    const run = getRunState();
    const view = getViewState();
    const name = autoName(run, view);
    saveBtn.disabled = true;
    try {
        await withStore((store) => store.save({ file: fileName, name, run, view }));
        saveBtn.textContent = '★';
        window.setTimeout(() => { saveBtn.textContent = '☆'; }, 1200);
        toast.newMessage(`Saved favorite: ${name}`);
    } catch (error) {
        reportError(error);
    } finally {
        saveBtn.disabled = false;
        await render();
    }
}

function apply(favorite: Favorite) {
    if (favorite.run) applyRunState(favorite.run);
    applyViewState(favorite.view);
    toast.newMessage(`Loaded favorite: ${favorite.name}`);
}

async function remove(favorite: Favorite) {
    try {
        await shownFrom.remove(favorite.id);
    } catch (error) {
        reportError(error);
    }
    await render();
}

async function render() {
    if (!fileName) return;
    const thisRender = ++renderCount;
    let favorites: Favorite[] = [];
    let from: FavoriteStore = localStore;
    try {
        ({ result: favorites, store: from } = await withStore((store) => store.list(fileName)));
    } catch (error) {
        reportError(error);
        if (!auth.getUser()) favorites = await localStore.list(fileName); // the 401 signed us out
    }
    if (thisRender !== renderCount) return; // a newer render (file or account change) took over
    shown = favorites;
    shownFrom = from;
    lastApplied = Math.min(lastApplied, shown.length - 1);

    list.replaceChildren();
    for (const favorite of favorites) {
        const row = document.createElement('div');
        row.className = 'favorite-row';
        const open = document.createElement('button');
        open.type = 'button';
        open.className = 'favorite-open';
        open.title = 'Load this view';
        const name = document.createElement('span');
        name.textContent = favorite.name;
        const when = document.createElement('small');
        when.textContent = new Date(favorite.savedAt).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
        open.append(name, when);
        open.onclick = () => apply(favorite);
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'delete-btn';
        del.title = 'Delete';
        del.textContent = '×';
        del.onclick = () => remove(favorite);
        row.append(open, del);
        list.appendChild(row);
    }
}

// The first time an account signs in on this browser, offer to copy the favorites saved here into it
function offerCopy(user: auth.User) {
    const local = loadAll();
    const offeredKey = OFFERED_KEY + user.id;
    if (local.length === 0 || localStorage.getItem(offeredKey)) return;
    localStorage.setItem(offeredKey, '1');

    const count = copyDialog.querySelector('[data-count]') as HTMLElement;
    count.textContent = `${local.length} favorite${local.length > 1 ? 's' : ''}`;
    copyDialog.showModal();
    copyDialog.onclose = async () => {
        if (copyDialog.returnValue !== 'copy') return;
        let copied = 0;
        try {
            // Oldest first, so the account keeps the same order
            for (const favorite of [...local].sort((a, b) => a.savedAt - b.savedAt)) {
                await apiStore.save(favorite);
                copied++;
            }
            toast.newMessage(`Copied ${copied} favorite${copied === 1 ? '' : 's'} into your account`);
        } catch (error) {
            toast.newError(`Copied ${copied} of ${local.length} favorites, then failed: ${error instanceof Error ? error.message : error}`);
        }
        await render();
    };
}

// Called once at startup
export function initFavorites() {
    saveBtn.onclick = saveCurrent;
    input.registerAction({ id: 'favorites.save', label: 'Save favorite', group: 'Favorites', run: () => { saveCurrent(); } });
    input.registerAction({
        id: 'favorites.step', label: 'Step through favorites', group: 'Favorites',
        run: (step) => {
            if (shown.length === 0) {
                toast.newMessage('No favorites saved for this file yet');
                return;
            }
            lastApplied = (lastApplied + step + shown.length) % shown.length;
            apply(shown[lastApplied]);
        },
    });
    input.attachBindButton(document.getElementById('favorites-step-bind') as HTMLButtonElement, 'favorites.step');

    auth.onAuthChange((user) => {
        offline = false;
        lastApplied = -1;
        if (user) offerCopy(user);
        render();
    });
}

// Called for each newly loaded file
export function setFavoritesFile(name: string) {
    fileName = name;
    lastApplied = -1;
    saveBtn.disabled = false;
    render();
}

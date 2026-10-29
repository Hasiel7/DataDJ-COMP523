import * as toast from './toast';
import * as input from './inputManager';
import { getViewState, applyViewState, type ViewState } from './header';
import { getRunState, applyRunState, describeRun, type RunState } from './runs';

// Saved views, kept in this browser per file name: the result-switcher run
// (method and parameters) plus axes, color, filters and hidden legend entries.

const STORAGE_KEY = 'datadj.favorites.v1';

interface Favorite {
    id: string;
    file: string;
    name: string;
    savedAt: number;
    run: RunState | null;
    view: ViewState;
}

let fileName = '';
let lastApplied = -1; // for stepping through favorites on a knob

const saveBtn = document.getElementById('favorite-save') as HTMLButtonElement;
const list = document.getElementById('favorites-list') as HTMLDivElement;

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

function forThisFile(): Favorite[] {
    return loadAll().filter((f) => f.file === fileName);
}

function autoName(run: RunState | null, view: ViewState): string {
    const parts = [run ? describeRun(run) : `${view.x || '–'} × ${view.y || '–'}`];
    if (view.color) parts.push(`color ${view.color}`);
    const activeFilters = view.filters.filter((f) => f.value !== '').length;
    if (activeFilters) parts.push(`${activeFilters} filter${activeFilters > 1 ? 's' : ''}`);
    if (view.hiddenCategories.length) parts.push(`${view.hiddenCategories.length} hidden`);
    return parts.join(' · ');
}

function saveCurrent() {
    if (!fileName) return;
    const run = getRunState();
    const view = getViewState();
    const favorite: Favorite = { id: crypto.randomUUID(), file: fileName, name: autoName(run, view), savedAt: Date.now(), run, view };
    saveAll([...loadAll(), favorite]);
    render();
    saveBtn.textContent = '★';
    window.setTimeout(() => { saveBtn.textContent = '☆'; }, 1200);
    toast.newMessage(`Saved favorite: ${favorite.name}`);
}

function apply(favorite: Favorite) {
    if (favorite.run) applyRunState(favorite.run);
    applyViewState(favorite.view);
    toast.newMessage(`Loaded favorite: ${favorite.name}`);
}

function remove(id: string) {
    saveAll(loadAll().filter((f) => f.id !== id));
    render();
}

function render() {
    list.replaceChildren();
    for (const favorite of forThisFile()) {
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
        del.onclick = () => remove(favorite.id);
        row.append(open, del);
        list.appendChild(row);
    }
}

// Called once at startup
export function initFavorites() {
    saveBtn.onclick = saveCurrent;
    input.registerAction({ id: 'favorites.save', label: 'Save favorite', group: 'Favorites', run: () => saveCurrent() });
    input.registerAction({
        id: 'favorites.step', label: 'Step through favorites', group: 'Favorites',
        run: (step) => {
            const favorites = forThisFile();
            if (favorites.length === 0) {
                toast.newMessage('No favorites saved for this file yet');
                return;
            }
            lastApplied = (lastApplied + step + favorites.length) % favorites.length;
            apply(favorites[lastApplied]);
        },
    });
    input.attachBindButton(document.getElementById('favorites-step-bind') as HTMLButtonElement, 'favorites.step');
}

// Called for each newly loaded file
export function setFavoritesFile(name: string) {
    fileName = name;
    lastApplied = -1;
    saveBtn.disabled = false;
    render();
}

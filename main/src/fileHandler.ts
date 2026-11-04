import Papa from 'papaparse'
import { setupHeader } from './header';
import { initRuns } from './runs';
import { setFavoritesFile } from './favorites';

// Open CSV and the sample menu both load through here
export async function loadFile(file: File) {
    if (!file.name.endsWith('.csv')) {
        throw new Error("File must be a CSV.")
    }

    const data: string[][] = await parseCSV(file)
    setupHeader(data)
    initRuns()
    setFavoritesFile(file.name)
}

function parseCSV(file: File): Promise<any[]> {
    return new Promise((resolve, reject) => {
        Papa.parse(file, {
            header: false,
            skipEmptyLines: true,
            complete: (results) => {
                resolve(results.data);
            },
            error: (error) => {
                reject(error);
            }
        });
    });
}

export { parseCSV }
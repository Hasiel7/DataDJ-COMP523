import Papa from 'papaparse'
import { setupHeader } from './header';
import { initRuns } from './runs';
import { setFavoritesFile } from './favorites';

const MAX_CSV_BYTES = 32 * 1024 * 1024;
const MAX_CSV_CELLS = 2_000_000;

// Open CSV and the sample menu both load through here
export async function loadFile(file: File) {
    if (!file.name.endsWith('.csv')) {
        throw new Error("File must be a CSV.")
    }

    const data: string[][] = await parseCSV(file)
    if (data.length < 2) {
        throw new Error(`No CSV data rows could be read from ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB). The file may be empty or too large for the browser. Try a smaller export.`)
    }
    setupHeader(data)
    initRuns()
    setFavoritesFile(file.name)
}

function parseCSV(file: File): Promise<string[][]> {
    if (file.size > MAX_CSV_BYTES) {
        return Promise.reject(new Error(
            `This CSV is ${(file.size / 1024 / 1024).toFixed(1)} MiB. The explorer accepts regular data CSVs up to 32 MiB. For a multi-run batch, use Open batch CSV to upload and browse one run at a time.`
        ));
    }
    return new Promise((resolve, reject) => {
        const rows: string[][] = [];
        let cells = 0;
        let failed = false;
        Papa.parse<string[]>(file, {
            header: false,
            skipEmptyLines: true,
            chunkSize: 1024 * 1024,
            chunk: (results, parser) => {
                const error = results.errors.find(error => error.code !== 'UndetectableDelimiter');
                if (error) {
                    failed = true;
                    reject(new Error(error.message));
                    parser.abort();
                    return;
                }
                for (const row of results.data) {
                    cells += row.length;
                    if (cells > MAX_CSV_CELLS) {
                        failed = true;
                        rows.length = 0;
                        reject(new Error('This CSV has too many cells for the explorer. Export fewer runs or fewer observations.'));
                        parser.abort();
                        return;
                    }
                    rows.push(row);
                }
            },
            complete: () => {
                if (!failed) resolve(rows);
            },
            error: (error) => {
                reject(error);
            }
        });
    });
}

export { parseCSV }
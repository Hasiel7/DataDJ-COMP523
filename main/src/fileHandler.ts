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

// Original dataset is stored separately from the plotted dataset.
let originalData: string[][] | null = null;

export async function loadOriginalFile(
    file: File,
    hasHeaders: boolean
): Promise<number> {
    if (!file.name.toLowerCase().endsWith('.csv')) {
        throw new Error('Original dataset must be a CSV file.');
    }

    const data = await parseCSV(file) as string[][];

    if (data.length === 0 || (hasHeaders && data.length < 2)) {
        throw new Error('Original dataset must contain at least one data row.');
    }

    if (!hasHeaders) {
        const columnCount = data[0].length;

        const generatedHeaders = Array.from(
            { length: columnCount },
            (_, i) => `column_${i}`
        );

        data.unshift(generatedHeaders);
    }

    originalData = data;
    return data.length - 1;
}

export function getOriginalData(): string[][] | null {
    return originalData;
}


export function matchOriginalRows(
    processedData: string[][],
    idColumn: string = 'index'
): number[] {
    if (!originalData) {
        throw new Error('No original dataset has been uploaded.');
    }

    const idColumnIndex = processedData[0].indexOf(idColumn);

    if (idColumnIndex === -1) {
        throw new Error(`Processed dataset is missing the "${idColumn}" column.`);
    }

    const originalRowCount = originalData.length - 1;
    const matchedRows: number[] = [];

    for (let i = 1; i < processedData.length; i++) {
        const rowId = Number(processedData[i][idColumnIndex]);

        if (!Number.isSafeInteger(rowId) ||
            rowId < 0 ||
            rowId >= originalRowCount) {
            throw new Error(
                `Invalid row ID "${processedData[i][idColumnIndex]}" at processed CSV row ${i + 1}.`
            );
        }

        matchedRows.push(rowId);
    }

    return matchedRows;
}

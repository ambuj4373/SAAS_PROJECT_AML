/**
 * Entity Type Detection Tests
 * Verifies the fixed detection logic works correctly
 */

function detectEntityType(q) {
  q = (q || '').trim().replace(/\s/g, '');
  if (!q) return null;
  
  // Extract prefix (letters) and digits
  const match = q.match(/^([A-Z]*)(\d+)$/i);
  if (!match) return null;
  
  const prefix = (match[1] || '').toUpperCase();
  const digitsOnly = match[2];
  
  // Charity: 1-7 digits, no prefix
  if (prefix === '' && digitsOnly.length >= 1 && digitsOnly.length <= 7) {
    return { type: 'charity', id: q };
  }
  
  // Company: 8 digits (numeric) or 2 letters + 6 digits
  if ((prefix === '' && digitsOnly.length === 8) || 
      ((prefix === 'SC' || prefix === 'NI') && digitsOnly.length === 6)) {
    return { type: 'company', id: q };
  }
  
  return null;
}

// Test cases
const testCases = [
  // Valid charities
  { input: '220949', expected: 'charity', desc: 'British Red Cross' },
  { input: '1155899', expected: 'charity', desc: 'World Aid Convoy (7 digits)' },
  { input: '1', expected: 'charity', desc: 'Edge case: 1-digit charity' },
  { input: '1234567', expected: 'charity', desc: 'Edge case: 7-digit charity' },
  
  // Valid companies
  { input: '09238471', expected: 'company', desc: '8-digit company' },
  { input: '12345678', expected: 'company', desc: 'Example 8-digit company' },
  { input: 'SC123456', expected: 'company', desc: 'Scottish company' },
  { input: 'sc123456', expected: 'company', desc: 'Scottish company (lowercase)' },
  { input: 'NI123456', expected: 'company', desc: 'N.I. company' },
  
  // Invalid inputs
  { input: '123456789', expected: null, desc: '9 digits (too many)' },
  { input: 'SC12345', expected: null, desc: 'SC prefix but only 5 digits' },
  { input: 'XX123456', expected: null, desc: 'Invalid prefix XX' },
  { input: '', expected: null, desc: 'Empty input' },
  { input: 'ABC', expected: null, desc: 'No digits' },
  { input: '12345', expected: 'charity', desc: '5 digits → charity' },
  { input: '123456', expected: 'charity', desc: '6 digits → charity' },
];

console.log('=== Entity Type Detection Tests ===\n');

let passed = 0;
let failed = 0;

testCases.forEach(({ input, expected, desc }) => {
  const result = detectEntityType(input);
  const resultType = result ? result.type : null;
  const success = resultType === expected;
  
  if (success) {
    console.log(`✅ PASS: ${desc}`);
    console.log(`   Input: "${input}" → ${resultType}`);
    passed++;
  } else {
    console.log(`❌ FAIL: ${desc}`);
    console.log(`   Input: "${input}"`);
    console.log(`   Expected: ${expected}, Got: ${resultType}`);
    failed++;
  }
  console.log();
});

console.log(`\n=== Summary ===`);
console.log(`Passed: ${passed}/${testCases.length}`);
console.log(`Failed: ${failed}/${testCases.length}`);

if (failed === 0) {
  console.log('\n🎉 All tests passed!');
} else {
  console.log('\n⚠️ Some tests failed.');
  process.exit(1);
}

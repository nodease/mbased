module.exports = {
  testDir: __dirname,
  testMatch: /.*\.spec\.js/,
  timeout: 120000,
  expect: {
    timeout: 30000,
  },
  use: {
    baseURL: process.env.NODEASE_BASE_URL || 'http://localhost',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  workers: 1,
};

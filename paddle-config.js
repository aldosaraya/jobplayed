/* Paddle Billing — put your IDs here and the checkout turns itself on.
 *
 * clientToken : Paddle dashboard → Developer Tools → Authentication
 *               sandbox tokens start with `test_`, production with `live_`
 * prices      : Paddle dashboard → Catalog → Products → Prices
 *               monthly = $4.99/mo price ID, annual = $48/year price ID
 *
 * Until both the token and the price for the selected plan exist, the button
 * shows an explicit "checkout is not configured" message instead of pretending.
 */
window.RADAR_PADDLE = {
  clientToken: "",
  environment: "sandbox", // "sandbox" | "production"
  prices: {
    monthly: "",
    annual: "",
  },
};

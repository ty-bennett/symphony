// Common first jobs, so owners see what to ask for. Keyed by business type;
// anything unknown gets the general list.
export type Starter = { title: string; detail: string; text: string }

const BAKERY: Starter[] = [
  {
    title: "Next week's staff schedule",
    detail: "From everyone's availability sheet",
    text: "Make next week's staff schedule from the availability sheet. Keep everyone under 40 hours.",
  },
  {
    title: "Menu items that lose money",
    detail: "Sales against ingredient costs",
    text: "Compare last quarter's sales with ingredient costs and list the menu items that lose money.",
  },
  {
    title: "This week's specials post",
    detail: "Instagram caption and photo ideas",
    text: "Write an Instagram post for this week's specials: pumpkin loaf and apple cider donuts.",
  },
  {
    title: "Sort supplier invoices",
    detail: "Totals by supplier, flags price jumps",
    text: "Total this month's supplier invoices by supplier and flag any price increases.",
  },
]

const GENERAL: Starter[] = [
  { title: "Reply to customer emails", detail: "Drafts only, nothing is sent", text: "Draft replies to this week's customer emails. Don't send anything." },
  { title: "Monthly sales summary", detail: "What sold, what didn't", text: "Summarize this month's sales and compare them with last month." },
  { title: "Social media post", detail: "A caption and photo ideas", text: "Write a social media post announcing this week's offer." },
  { title: "Sort invoices", detail: "Totals by vendor", text: "Total this month's invoices by vendor and flag anything unusual." },
]

export function startersFor(businessType: string): Starter[] {
  return /bak|caf|coffee|restaurant|food/i.test(businessType) ? BAKERY : GENERAL
}

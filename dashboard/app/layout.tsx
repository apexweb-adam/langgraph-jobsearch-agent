import "./globals.css";

export const metadata = {
  title: "Job Search Dashboard",
  description: "Scored job matches from your discovery agent",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

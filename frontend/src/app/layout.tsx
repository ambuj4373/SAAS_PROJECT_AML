import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'Know Your Charity - AML Due Diligence',
  description: 'Professional AML/KYC screening for UK companies and charities',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body className="bg-white text-gray-900">
        {children}
      </body>
    </html>
  )
}

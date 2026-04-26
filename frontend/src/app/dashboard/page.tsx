'use client'

import { useState } from 'react'
import { Search, FileText, Download, Eye } from 'lucide-react'
import Link from 'next/link'

export default function Dashboard() {
  const [searchType, setSearchType] = useState<'company' | 'charity'>('company')
  const [searchQuery, setSearchQuery] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!searchQuery.trim()) return

    setIsLoading(true)
    // Call your backend API here
    console.log(`Searching ${searchType}: ${searchQuery}`)
    setTimeout(() => setIsLoading(false), 2000)
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 sticky top-0 z-40">
        <div className="max-w-6xl mx-auto px-4 py-4 flex justify-between items-center">
          <Link href="/" className="text-2xl font-bold text-teal-700">🛡️ Know Your Charity</Link>
          <div className="space-x-4">
            <button className="text-gray-700 hover:text-teal-700">Account</button>
            <button className="text-gray-700 hover:text-teal-700">Logout</button>
          </div>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-4 py-12">
        <div className="grid lg:grid-cols-4 gap-8">
          {/* Sidebar */}
          <div className="lg:col-span-1">
            <div className="bg-white p-6 rounded-lg border border-gray-200">
              <h3 className="font-bold text-lg mb-4">Your Plan</h3>
              <p className="text-gray-600 text-sm mb-4">Pay as you go</p>
              <p className="text-3xl font-bold text-teal-700 mb-4">£19</p>
              <p className="text-gray-600 text-sm mb-6">per report</p>
              <button className="w-full bg-teal-700 text-white py-2 rounded-lg hover:bg-teal-800 text-sm">
                Upgrade Plan
              </button>
            </div>

            <div className="bg-white p-6 rounded-lg border border-gray-200 mt-6">
              <h3 className="font-bold text-lg mb-4">Recent Reports</h3>
              <ul className="space-y-2 text-sm">
                <li className="text-gray-600 hover:text-teal-700 cursor-pointer">Wise Ltd</li>
                <li className="text-gray-600 hover:text-teal-700 cursor-pointer">Charity XYZ</li>
              </ul>
            </div>
          </div>

          {/* Main Content */}
          <div className="lg:col-span-3">
            {/* Search Section */}
            <div className="bg-white p-8 rounded-lg border border-gray-200 mb-8">
              <h1 className="text-3xl font-bold mb-6">Generate a Report</h1>
              
              <div className="mb-6">
                <label className="block text-sm font-medium text-gray-700 mb-3">Search Type</label>
                <div className="flex gap-4">
                  <label className="flex items-center">
                    <input
                      type="radio"
                      value="company"
                      checked={searchType === 'company'}
                      onChange={(e) => setSearchType(e.target.value as 'company' | 'charity')}
                      className="mr-2"
                    />
                    <span className="text-gray-700">🏢 Company (Companies House)</span>
                  </label>
                  <label className="flex items-center">
                    <input
                      type="radio"
                      value="charity"
                      checked={searchType === 'charity'}
                      onChange={(e) => setSearchType(e.target.value as 'company' | 'charity')}
                      className="mr-2"
                    />
                    <span className="text-gray-700">❤️ Charity (Charity Commission)</span>
                  </label>
                </div>
              </div>

              <form onSubmit={handleSearch} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {searchType === 'company' ? 'Companies House Number' : 'Charity Number'}
                  </label>
                  <div className="relative">
                    <input
                      type="text"
                      placeholder={searchType === 'company' ? 'e.g., 13211214' : 'e.g., 327274'}
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:border-teal-500 focus:ring-2 focus:ring-teal-200"
                    />
                    <Search className="absolute right-3 top-3.5 text-gray-400 w-5 h-5" />
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={isLoading || !searchQuery}
                  className="w-full bg-teal-700 text-white py-3 rounded-lg hover:bg-teal-800 disabled:bg-gray-400 disabled:cursor-not-allowed font-semibold transition"
                >
                  {isLoading ? 'Generating Report...' : 'Generate Report'}
                </button>
              </form>

              <p className="text-sm text-gray-500 mt-4">⏱️ Reports typically complete in 60 seconds</p>
            </div>

            {/* Reports List */}
            <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
              <div className="p-6 border-b border-gray-200">
                <h2 className="text-xl font-bold">Your Reports</h2>
              </div>
              <table className="w-full">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-6 py-3 text-left text-sm font-semibold text-gray-700">Entity Name</th>
                    <th className="px-6 py-3 text-left text-sm font-semibold text-gray-700">Type</th>
                    <th className="px-6 py-3 text-left text-sm font-semibold text-gray-700">Risk Level</th>
                    <th className="px-6 py-3 text-left text-sm font-semibold text-gray-700">Date</th>
                    <th className="px-6 py-3 text-left text-sm font-semibold text-gray-700">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-gray-200 hover:bg-gray-50">
                    <td className="px-6 py-4 text-sm">Wise Ltd</td>
                    <td className="px-6 py-4 text-sm">Company</td>
                    <td className="px-6 py-4"><span className="px-3 py-1 bg-green-100 text-green-800 rounded-full text-xs font-semibold">Low Risk</span></td>
                    <td className="px-6 py-4 text-sm text-gray-600">27 Apr 2026</td>
                    <td className="px-6 py-4 space-x-2">
                      <button className="text-teal-700 hover:text-teal-900"><Eye className="w-4 h-4 inline" /></button>
                      <button className="text-teal-700 hover:text-teal-900"><Download className="w-4 h-4 inline" /></button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

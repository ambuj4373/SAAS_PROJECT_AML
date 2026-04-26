'use client'

import Link from 'next/link'
import { Shield, Zap, TrendingUp, CheckCircle } from 'lucide-react'

export default function Home() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-teal-50 to-white">
      {/* Header */}
      <header className="border-b border-gray-200 sticky top-0 bg-white/95 backdrop-blur z-50">
        <nav className="max-w-6xl mx-auto px-4 py-4 flex justify-between items-center">
          <div className="text-2xl font-bold text-teal-700">🛡️ Know Your Charity</div>
          <div className="space-x-4">
            <Link href="/login" className="text-gray-700 hover:text-teal-700 px-4 py-2">Login</Link>
            <Link href="/dashboard" className="bg-teal-700 text-white px-4 py-2 rounded-lg hover:bg-teal-800">
              Dashboard
            </Link>
          </div>
        </nav>
      </header>

      {/* Hero */}
      <section className="max-w-6xl mx-auto px-4 py-24">
        <div className="text-center">
          <h1 className="text-5xl md:text-6xl font-bold mb-6 text-gray-900">
            Professional AML Due Diligence Made Simple
          </h1>
          <p className="text-xl text-gray-600 mb-8 max-w-2xl mx-auto">
            Generate comprehensive compliance reports for UK companies and charities in 60 seconds.
            Used by accountants, compliance teams, and grant-makers.
          </p>
          <div className="space-x-4 mb-12">
            <Link href="/dashboard" className="bg-teal-700 text-white px-8 py-4 rounded-lg hover:bg-teal-800 font-semibold inline-block">
              Start Free Trial
            </Link>
            <button className="border-2 border-teal-700 text-teal-700 px-8 py-4 rounded-lg hover:bg-teal-50 font-semibold">
              See Demo Video
            </button>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="bg-white py-20">
        <div className="max-w-6xl mx-auto px-4">
          <h2 className="text-3xl font-bold text-center mb-16">Powerful Features</h2>
          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6">
            {[
              { icon: Shield, title: 'Sanctions Screening', desc: 'OFAC, UN, EU real-time checks' },
              { icon: CheckCircle, title: 'Governance Check', desc: 'Director & trustee analysis' },
              { icon: Zap, title: 'Instant Reports', desc: 'Results in 60 seconds' },
              { icon: TrendingUp, title: 'Risk Scoring', desc: 'AI-powered assessment' },
            ].map((f, i) => (
              <div key={i} className="p-6 border border-gray-200 rounded-lg hover:shadow-lg hover:border-teal-200 transition">
                <f.icon className="w-8 h-8 text-teal-700 mb-4" />
                <h3 className="font-bold text-lg mb-2">{f.title}</h3>
                <p className="text-gray-600 text-sm">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section className="py-20 bg-gray-50">
        <div className="max-w-4xl mx-auto px-4 text-center">
          <h2 className="text-3xl font-bold mb-12">Simple Pricing</h2>
          <div className="grid md:grid-cols-2 gap-8">
            <div className="bg-white p-8 rounded-lg border border-gray-200">
              <h3 className="text-2xl font-bold mb-4">Pay as you go</h3>
              <p className="text-4xl font-bold text-teal-700 mb-4">£19</p>
              <p className="text-gray-600 mb-6">per report</p>
              <Link href="/dashboard" className="w-full bg-teal-700 text-white py-3 rounded-lg hover:bg-teal-800 block font-semibold">
                Get Started
              </Link>
            </div>
            <div className="bg-white p-8 rounded-lg border-2 border-teal-700 shadow-lg">
              <h3 className="text-2xl font-bold mb-4">Subscription</h3>
              <p className="text-4xl font-bold text-teal-700 mb-4">£49</p>
              <p className="text-gray-600 mb-6">per month (10 reports)</p>
              <button className="w-full bg-teal-700 text-white py-3 rounded-lg hover:bg-teal-800 font-semibold">
                Start Free Trial
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="bg-teal-700 text-white py-16">
        <div className="max-w-4xl mx-auto px-4 text-center">
          <h2 className="text-3xl font-bold mb-4">Ready to streamline your AML compliance?</h2>
          <p className="text-teal-100 mb-8">Join hundreds of UK professionals using Know Your Charity</p>
          <Link href="/dashboard" className="bg-white text-teal-700 px-8 py-3 rounded-lg font-bold hover:bg-teal-50 inline-block">
            Start Your Free Trial
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="bg-gray-900 text-gray-400 py-12">
        <div className="max-w-6xl mx-auto px-4">
          <div className="grid md:grid-cols-4 gap-8 mb-8">
            <div>
              <h3 className="font-bold text-white mb-4">Product</h3>
              <ul className="space-y-2 text-sm"><li><a href="#" className="hover:text-white">Features</a></li></ul>
            </div>
            <div>
              <h3 className="font-bold text-white mb-4">Legal</h3>
              <ul className="space-y-2 text-sm"><li><a href="#" className="hover:text-white">Privacy Policy</a></li></ul>
            </div>
            <div>
              <h3 className="font-bold text-white mb-4">Company</h3>
              <ul className="space-y-2 text-sm"><li><a href="#" className="hover:text-white">About</a></li></ul>
            </div>
            <div>
              <h3 className="font-bold text-white mb-4">Status</h3>
              <p className="text-sm">🟢 All systems operational</p>
            </div>
          </div>
          <div className="border-t border-gray-700 pt-8 text-sm text-center">
            <p>&copy; 2026 Know Your Charity. All rights reserved.</p>
          </div>
        </div>
      </footer>
    </div>
  )
}

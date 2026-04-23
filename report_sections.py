import asyncio
from gov_aggregator.services import crawl_site_keys


async def main():
    sites = [
        ('advertising-standards-council-of-india', 'ASCI - Advertising Standards Council of India'),
        ('bis', 'BIS - Bureau of Indian Standards'),
    ]

    print('=' * 100)
    print('SECTION SCRAPING REPORT (Items after January 2026 only)')
    print('=' * 100)

    for site_key, site_name in sites:
        print()
        print('SITE:', site_name)
        print('-' * 100)
        header = f"{'Section':<35} {'Total':>10} {'With Date':>12} {'PDFs':>8} {'New':>8}"
        print(header)
        print('-' * 100)

        result = await crawl_site_keys([site_key], use_cache=False)
        items = result['items']

        sections = {}
        for item in items:
            sec = item.get('section_label', 'Main')
            if sec not in sections:
                sections[sec] = {'total': 0, 'with_date': 0, 'is_pdf': 0, 'new': 0}
            sections[sec]['total'] += 1
            if item.get('publish_date'):
                sections[sec]['with_date'] += 1
            if item.get('is_pdf'):
                sections[sec]['is_pdf'] += 1
            if item.get('is_new'):
                sections[sec]['new'] += 1

        for sec, stats in sections.items():
            row = f"{sec:<35} {stats['total']:>10} {stats['with_date']:>12} {stats['is_pdf']:>8} {stats['new']:>8}"
            print(row)

        total = sum(s['total'] for s in sections.values())
        total_date = sum(s['with_date'] for s in sections.values())
        total_pdf = sum(s['is_pdf'] for s in sections.values())
        total_new = sum(s['new'] for s in sections.values())
        print('-' * 100)
        total_row = f"{'TOTAL':<35} {total:>10} {total_date:>12} {total_pdf:>8} {total_new:>8}"
        print(total_row)

    print()
    print('=' * 100)


if __name__ == '__main__':
    asyncio.run(main())
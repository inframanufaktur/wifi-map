use super::*;

#[test]
fn netstat_link_rows_allow_an_empty_address_column() {
    let text = "Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll\nen0 1500 <Link#14> aa:bb:cc:dd:ee:ff 19 0 25850300535 8 0 3852569809 0\nutun6 1420 <Link#27> 6 0 8524156520 4 0 2180756050 0";
    assert_eq!(
        parse_netstat_ib(text, "en0"),
        Some((25850300535, 3852569809))
    );
    assert_eq!(
        parse_netstat_ib(text, "utun6"),
        Some((8524156520, 2180756050))
    );
    assert_eq!(parse_netstat_ib(text, "en9"), None);
}

#[test]
fn traffic_rates_rebaseline_after_missing_or_reset_counters() {
    let mut sampler = TrafficSampler::default();
    assert_eq!(sampler.record(Some((1000, 500)), Duration::ZERO), None);
    assert_eq!(
        sampler.record(Some((126000, 25500)), Duration::from_secs(1)),
        Some((1., 0.2))
    );
    assert_eq!(sampler.record(None, Duration::from_secs(2)), None);
    assert_eq!(
        sampler.record(Some((100, 100)), Duration::from_secs(3)),
        None
    );
    assert_eq!(sampler.record(Some((50, 50)), Duration::from_secs(4)), None);
}

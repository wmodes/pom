"""Tests for pom.conductor.address_finder: the machine's usable IPv4 addresses, from a described network (no real one needed)."""
import logging
import socket
from types import SimpleNamespace

import pytest

from pom.conductor import AddressFinder


def record(address, family=socket.AF_INET):
    return SimpleNamespace(family=family, address=address)


def finder_for(interfaces):
    return AddressFinder(lambda: interfaces)


class TestWhatCounts:
    def test_a_home_network_address_is_found(self):
        assert finder_for({"wlan0": [record("192.168.1.23")]}).find() == ["192.168.1.23"]

    def test_loopback_is_left_out(self):
        assert finder_for({"lo": [record("127.0.0.1")]}).find() == []

    def test_every_loopback_address_is_left_out_not_only_the_usual_one(self):
        assert finder_for({"lo": [record("127.0.1.1")]}).find() == []

    def test_a_link_local_address_is_left_out_because_it_means_dhcp_failed(self):
        assert finder_for({"wlan0": [record("169.254.12.7")]}).find() == []

    def test_the_unspecified_and_multicast_addresses_are_left_out(self):
        assert finder_for({"x": [record("0.0.0.0"), record("224.0.0.1")]}).find() == []

    def test_ipv6_and_mac_address_records_are_left_out(self):
        interfaces = {"wlan0": [record("fe80::1", socket.AF_INET6), record("aa:bb:cc:dd:ee:ff", socket.AF_PACKET if hasattr(socket, "AF_PACKET") else -1),
                                record("10.0.0.5")]}
        assert finder_for(interfaces).find() == ["10.0.0.5"]

    def test_an_ipv6_address_is_left_out_even_when_it_is_a_usable_kind(self):
        assert finder_for({"wlan0": [record("2001:db8::1", socket.AF_INET6)]}).find() == []

    def test_a_record_with_junk_in_it_does_not_hide_the_good_ones_after_it(self):
        assert finder_for({"wlan0": [record("junk"), record("10.0.0.5")]}).find() == ["10.0.0.5"]

    def test_text_that_is_not_an_address_is_left_out(self):
        assert finder_for({"x": [record("not an address"), record("")]}).find() == []

    def test_no_interfaces_is_no_addresses(self):
        assert finder_for({}).find() == []


class TestMoreThanOne:
    def test_every_usable_address_is_listed_in_interface_name_order(self):
        interfaces = {"wlan0": [record("192.168.1.23")], "eth0": [record("10.0.0.5")], "lo": [record("127.0.0.1")]}
        assert finder_for(interfaces).find() == ["10.0.0.5", "192.168.1.23"]

    def test_the_hotspot_address_is_found_with_no_default_route(self):
        assert finder_for({"wlan0": [record("10.41.0.1")]}).find() == ["10.41.0.1"]

    def test_one_interface_with_two_addresses_gives_both(self):
        assert finder_for({"eth0": [record("10.0.0.5"), record("10.0.0.6")]}).find() == ["10.0.0.5", "10.0.0.6"]


class TestItNeverRaises:
    def test_a_failing_lookup_is_no_addresses_and_a_warning(self, caplog):
        def broken():
            raise OSError("no permission")
        with caplog.at_level(logging.WARNING, logger="pom.conductor.address_finder"):
            assert AddressFinder(broken).find() == []
        assert any("OSError" in r.getMessage() for r in caplog.records)

    def test_a_record_of_the_wrong_shape_is_no_addresses_not_a_crash(self):
        assert finder_for({"x": [object()]}).find() == []


def test_the_real_lookup_returns_a_list_of_text_addresses():
    found = AddressFinder().find()
    assert isinstance(found, list) and all(isinstance(a, str) and not a.startswith("127.") for a in found)


@pytest.mark.parametrize("address", ["192.168.0.1", "10.10.13.255", "172.16.4.2", "100.64.0.9"])
def test_private_and_shared_ranges_are_all_usable(address):
    assert finder_for({"x": [record(address)]}).find() == [address]

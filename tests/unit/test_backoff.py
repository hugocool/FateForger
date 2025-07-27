from src.haunters import BaseHaunter


class TestBackoff:
    def test_next_delay_growth_and_cap(self):
        assert BaseHaunter.next_delay(0, base=10, cap=40) == 10
        assert BaseHaunter.next_delay(1, base=10, cap=40) == 20
        assert BaseHaunter.next_delay(2, base=10, cap=40) == 40
        assert BaseHaunter.next_delay(3, base=10, cap=40) == 40

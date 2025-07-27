from src.haunters import BaseHaunter


class TestBaseHaunter:
    def test_next_delay_formula(self):
        assert BaseHaunter.next_delay(0) == 5
        assert BaseHaunter.next_delay(1) == 10
        assert BaseHaunter.next_delay(4) == 80
        assert BaseHaunter.next_delay(6) == 120

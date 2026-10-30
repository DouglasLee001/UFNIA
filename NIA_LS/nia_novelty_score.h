#pragma once
#include <cassert>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <random>
#include <string>
#include <vector>

namespace nia {
enum class StepnovelChoice { softmax, break_cost, rank, random };
inline const char* stepnovel_choice_name(StepnovelChoice choice){
    return choice==StepnovelChoice::softmax?"softmax":choice==StepnovelChoice::break_cost?"break":choice==StepnovelChoice::rank?"rank":"random";
}
inline bool parse_stepnovel_choice(const std::string& name,StepnovelChoice& choice){
    if(name=="softmax")choice=StepnovelChoice::softmax;
    else if(name=="break")choice=StepnovelChoice::break_cost;
    else if(name=="rank")choice=StepnovelChoice::rank;
    else if(name=="random")choice=StepnovelChoice::random;
    else return false;
    return true;
}
inline bool valid_stepnovel_beta(double beta){return std::isfinite(beta) && beta>0.0 && beta<=8.0;}
struct NoveltyChoice {
    int var;
    __int128_t delta;
    int score;
    uint64_t age; // Last-move timestamp: smaller means less recently moved.
    uint64_t break_cost=0; // Sum of weights of satisfied clauses made unsatisfied.
};

struct AdaptiveSoftmax {
    static constexpr double minimum=0.1,maximum=2.0;
    double initial_temperature=0.25;
    uint64_t window=20000; // Search steps, starting at the first eligible sampling call.
    double temperature=0.25;
    bool active=false;
    int best=0;
    uint64_t anchor_step=0,heat_windows=0,improvements=0;

    static bool valid_temperature(double value){
        return std::isfinite(value) && value>=minimum && value<=maximum;
    }
    // Called for every new historical best, including improvements obtained by
    // ordinary moves or LIA repair. Restart-local progress must not cool us.
    void observe_progress(uint64_t step,int historical_best){
        if(active && historical_best<best){
            best=historical_best;
            temperature=std::max(minimum,temperature*0.8);
            anchor_step=step;++improvements;
        }
    }
    void prepare(uint64_t step,int historical_best){
        assert(valid_temperature(initial_temperature) && window>0);
        if(!active){
            active=true;temperature=initial_temperature;best=historical_best;
            anchor_step=step;return;
        }
        observe_progress(step,historical_best);
        assert(step>=anchor_step); // The solver's step counter spans restarts.
        const uint64_t windows=(step-anchor_step)/window;
        if(!windows)return;
        anchor_step+=windows*window;heat_windows+=windows;
        // At most 14 multiplications from the lower to the upper bound. This
        // also handles a long inactive interval without a large loop or pow.
        for(uint64_t i=0;i<windows && temperature<maximum;++i)
            temperature=std::min(maximum,temperature*1.25);
    }
};

// Normalize by the median strictly positive gap from the best primary score.
// Shift invariance and positive weight-scale invariance (integer scores) avoid
// a changing clause-weight scale acting as an unintended temperature schedule.
inline double softmax_score_scale(const std::vector<NoveltyChoice>& choices,int maximum){
    std::array<double,45> gaps;size_t count=0;
    for(const auto& c:choices)if(c.score<maximum)gaps[count++]=double(maximum)-double(c.score);
    if(!count)return 1.0;
    std::sort(gaps.begin(),gaps.begin()+count);
    return std::max(1.0,count%2?gaps[count/2]:(gaps[count/2-1]+gaps[count/2])*0.5);
}

template<class Random>
size_t select_novelty_by_softmax(const std::vector<NoveltyChoice>& choices,Random& random,
                               double temperature,double* score_scale=nullptr){
    assert(!choices.empty() && choices.size()<=45 && AdaptiveSoftmax::valid_temperature(temperature));
    if(choices.size()==1){if(score_scale)*score_scale=1.0;return 0;}
    int maximum=choices[0].score;
    for(const auto& c:choices)maximum=std::max(maximum,c.score);
    const double scale=softmax_score_scale(choices,maximum);
    if(score_scale)*score_scale=scale;
    std::array<double,45> weights;double total=0;
    for(size_t i=0;i<choices.size();++i){
        // Convert before subtracting: INT_MIN..INT_MAX gaps exceed signed int.
        weights[i]=std::exp(((double(choices[i].score)-double(maximum))/scale)/temperature);
        total+=weights[i];
    }
    // Best-score weights are exactly 1, so total cannot underflow to zero.
    // Negligible tail weights may underflow; do not add an exploration floor.
    const double draw=std::generate_canonical<double,53>(random)*total;
    double cumulative=0;size_t last_positive=0;
    for(size_t i=0;i<choices.size();++i){
        if(weights[i]>0)last_positive=i;
        cumulative+=weights[i];
        if(draw<cumulative)return i;
    }
    return last_positive; // Roundoff must not select a zero-weight tail.
}

// P(i) is proportional to (1+B_i)^(-beta), regardless of rank or score sign.
// Scaling by the largest weight keeps the total finite and positive even when
// every break cost is large. With uint64 costs and beta<=8, weights stay positive.
template<class Random>
size_t select_novelty_by_break(const std::vector<NoveltyChoice>& choices,Random& random,double beta){
    assert(!choices.empty() && choices.size()<=45 && valid_stepnovel_beta(beta));
    if(choices.size()==1)return 0;
    uint64_t minimum=choices[0].break_cost;
    for(const auto& choice:choices)if(choice.break_cost<minimum)minimum=choice.break_cost;
    const double scale=1.0+double(minimum);
    std::array<double,45> weights;double total=0;
    for(size_t i=0;i<choices.size();++i){
        const double ratio=scale/(1.0+double(choices[i].break_cost));
        weights[i]=beta==2.0?ratio*ratio:std::pow(ratio,beta);
        total+=weights[i];
    }
    const double draw=std::generate_canonical<double,53>(random)*total;
    double cumulative=0;
    for(size_t i=0;i<choices.size();++i){
        cumulative+=weights[i];
        if(draw<cumulative)return i;
    }
    return choices.size()-1;
}

// Lexicographic priority, not a weighted sum. Exploration can intentionally
// prefer an action with a lower primary score when randomly selected.
struct NoveltyScore {
    bool exploration;
    int primary;
    uint64_t last_move;

    bool better_than(const NoveltyScore& other) const {
        if(exploration!=other.exploration)return exploration;
        if(primary!=other.primary)return primary>other.primary;
        return last_move<other.last_move;
    }
};

// Requires the original nonempty, stably sorted candidate sequence (primary
// descending, timestamp ascending). Keep this ordering for random indices and
// exact ties, including duplicate candidates sampled with replacement.
template<class Random>
size_t select_novelty_by_score(const std::vector<NoveltyChoice>& choices,Random& random){
    assert(!choices.empty());
    size_t random_candidate=choices.size();
    // Only the 10% random branch gets exploration priority. Otherwise compare
    // primary score and age, even if the best variable was just moved.
    if(random()%100<10)random_candidate=random()%choices.size();

    auto score=[&](size_t i){
        const auto& choice=choices[i];
        return NoveltyScore{i==random_candidate,choice.score,choice.age};
    };
    size_t selected=0;
    auto best=score(0);
    for(size_t i=1;i<choices.size();++i){
        const auto candidate=score(i);
        if(candidate.better_than(best)){selected=i;best=candidate;}
    }
    return selected;
}

// Same stable primary-score/age ordering as the legacy selector. Rank is over
// candidate entries, including duplicates, not distinct variables or scores.
// N candidates receive N, N-1, ..., 1 tickets. No exponent or stall schedule.
// All ranks have positive mass; zero/negative primary scores remain eligible.
template<class Random>
size_t select_novelty_by_rank(const std::vector<NoveltyChoice>& choices,Random& random){
    assert(!choices.empty() && choices.size()<=45);
    const unsigned count=static_cast<unsigned>(choices.size());
    if(count==1)return 0;
    unsigned ticket=std::uniform_int_distribution<unsigned>(0,count*(count+1)/2-1)(random);
    for(unsigned i=0;i<count;++i){
        const unsigned weight=count-i;
        if(ticket<weight)return i;
        ticket-=weight;
    }
    assert(false); // Every ticket belongs to one candidate.
    return count-1;
}
}
